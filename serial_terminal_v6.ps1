param(
    [switch]$SelfTest,
    [switch]$WaveTest,
    [switch]$Headless,
    [string]$Port,
    [int]$Baud = 1500000,
    [string]$Protocol = 'FireWater',
    [double]$Seconds = 10,
    [string]$Out,
    [string]$HitPattern,
    [string]$TriggerFile,
    [string]$ExpectFile,
    [string]$RecordFile,
    [string]$ReplayFile,
    [string]$FlashCommand,
    [int]$FlashTimeoutMs = 300000,
    [string]$ResetMode,
    [string]$ResetCommand,
    [switch]$Dtr,
    [switch]$Rts,
    [int]$ControlPort = 9101,
    [switch]$NoControlPort,
    # 自检钩子：把主窗体渲染成 PNG 后自动退出。主题回归靠它，不靠人眼。
    # 用法：-ShotPath logs\main_dark.png -ShotTheme Dark
    [string]$ShotPath = '',
    [string]$ShotTheme = '',
    # screen = 真实抓屏（能拍到 ComboBox/滚动条/复选框这些系统主题绘制的控件）
    # bitmap = 窗体内 DrawToBitmap（拍不到上面那几类，但不受遮挡影响）
    # demo   = screen + 注入演示日志/查找高亮，续拍波形窗与 Modbus 监视页（README 用图）
    [ValidateSet('screen', 'bitmap', 'demo')]
    [string]$ShotMode = 'bitmap'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 应用根目录：模块被 dot-source 后，模块内的 $PSScriptRoot 是【模块自己】的目录
# （core\ 或 ui\），直接用会把 gui_settings.txt / logs / 波形预设等写到模块目录下。
# 所有涉及应用目录的路径一律用 $script:app_root，不用 $PSScriptRoot。
$script:app_root = $PSScriptRoot

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName Microsoft.VisualBasic
# ---------- 模块加载（V6 模块化：commit 3-5，纯移动，零行为变化） ----------
# 模块文件均为 UTF-8 带 BOM，PS5.1 可安全 dot-source。
# 注意：不能用函数包装 ". $path" —— 函数内 dot-source 会让定义落入函数局部作用域，
# 函数返回后全部消失。必须在脚本顶层的作用域里直接 dot-source，定义才进脚本作用域。
# 加载顺序 = 依赖顺序：状态与 IO → 协议 → 无头/控制 → UI。
$script:module_list = @(
    'core\serial_io.ps1',
    'core\protocol.ps1',
    'core\fault_decode.ps1',
    'core\headless.ps1',
    'core\control_channel.ps1',
    'ui\theme.ps1',
    'ui\wave_form.ps1',
    'ui\main_form.ps1'
)
foreach ($script:module_rel in $script:module_list) {
    $script:module_path = Join-Path $PSScriptRoot $script:module_rel
    if (-not (Test-Path -LiteralPath $script:module_path)) {
        throw ('模块文件缺失: ' + $script:module_path)
    }
    try {
        . $script:module_path
    }
    catch {
        Write-Output ('模块加载失败: ' + $script:module_rel + ' — ' + $_.Exception.Message)
        throw
    }
}


# ============ WaveDsp：滤波与 FFT 的 C# 核心（PowerShell 循环慢，数值计算必须下沉）============
if (-not ('WaveDsp' -as [type])) {
    Add-Type -TypeDefinition @'
using System;

public static class WaveDsp
{
    // NaN 防护：用最近一个有效值填充，避免 NaN 经过滤波器扩散
    private static double[] Sanitize(double[] x)
    {
        double[] r = new double[x.Length];
        double last = 0.0;
        bool has = false;
        for (int i = 0; i < x.Length; i++)
        {
            double v = x[i];
            if (double.IsNaN(v) || double.IsInfinity(v))
            {
                r[i] = has ? last : 0.0;
            }
            else
            {
                r[i] = v;
                last = v;
                has = true;
            }
        }
        return r;
    }

    // 滑动平均（前缀和，O(n)）；window<2 返回副本
    public static double[] MovingAverage(double[] x, int window)
    {
        if (x.Length == 0) { return new double[0]; }
        double[] s = Sanitize(x);
        if (window < 2) { return s; }
        int win = window;
        double[] y = new double[s.Length];
        double sum = 0.0;
        for (int i = 0; i < s.Length; i++)
        {
            sum += s[i];
            if (i >= win) { sum -= s[i - win]; }
            int cnt = (i + 1) < win ? (i + 1) : win;
            y[i] = sum / cnt;
        }
        return y;
    }

    // 零相位一阶低通：前向 EMA + 反向 EMA（filtfilt 思路）
    // 相位对称抵消 → 无滞后；alpha ∈ (0,1]，越小截止越低
    public static double[] ZeroPhaseLowPass(double[] x, double alpha)
    {
        if (x.Length == 0) { return new double[0]; }
        double[] s = Sanitize(x);
        if (alpha >= 1.0) { return s; }
        if (alpha < 0.005) { alpha = 0.005; }
        int n = s.Length;
        double[] f = new double[n];
        double acc = s[0];
        for (int i = 0; i < n; i++) { acc += alpha * (s[i] - acc); f[i] = acc; }
        double[] y = new double[n];
        acc = f[n - 1];
        for (int i = n - 1; i >= 0; i--) { acc += alpha * (f[i] - acc); y[i] = acc; }
        return y;
    }

    // 实数 FFT 幅度谱：输入任意长度，取最长 2^k 段，加汉宁窗，
    // 返回前 n/2 个 bin 的单边幅度谱（已归一化），used 输出实际参与 FFT 的点数
    public static double[] MagnitudeSpectrum(double[] input, out int used)
    {
        used = 0;
        if (input == null || input.Length < 32) { return new double[0]; }
        double[] src = Sanitize(input);
        int n = 1;
        while (n * 2 <= src.Length) { n *= 2; }
        if (n < 32) { return new double[0]; }
        used = n;

        double[] re = new double[n];
        double[] im = new double[n];
        for (int i = 0; i < n; i++)
        {
            double w = 0.5 * (1.0 - Math.Cos(2.0 * Math.PI * i / (n - 1)));
            re[i] = src[i] * w;
        }

        // 位反转置换
        for (int i = 1, j = 0; i < n; i++)
        {
            int bit = n >> 1;
            for (; (j & bit) != 0; bit >>= 1) { j ^= bit; }
            j ^= bit;
            if (i < j)
            {
                double t = re[i]; re[i] = re[j]; re[j] = t;
                t = im[i]; im[i] = im[j]; im[j] = t;
            }
        }

        // 迭代基-2 蝶形
        for (int len = 2; len <= n; len <<= 1)
        {
            double ang = -2.0 * Math.PI / len;
            double wr = Math.Cos(ang);
            double wi = Math.Sin(ang);
            int half = len >> 1;
            for (int i = 0; i < n; i += len)
            {
                double cr = 1.0;
                double ci = 0.0;
                for (int k = 0; k < half; k++)
                {
                    double ur = re[i + k];
                    double ui = im[i + k];
                    double vr = re[i + k + half] * cr - im[i + k + half] * ci;
                    double vi = re[i + k + half] * ci + im[i + k + half] * cr;
                    re[i + k] = ur + vr;
                    im[i + k] = ui + vi;
                    re[i + k + half] = ur - vr;
                    im[i + k + half] = ui - vi;
                    double ncr = cr * wr - ci * wi;
                    ci = cr * wi + ci * wr;
                    cr = ncr;
                }
            }
        }

        double[] mag = new double[n >> 1];
        for (int i = 0; i < (n >> 1); i++)
        {
            mag[i] = 2.0 * Math.Sqrt(re[i] * re[i] + im[i] * im[i]) / n;
        }
        return mag;
    }

    // 幅度 → dB（20log10，满量程正弦 = 0 dB，底噪下限 -140）
    public static double[] ToDb(double[] mag)
    {
        double[] db = new double[mag.Length];
        for (int i = 0; i < mag.Length; i++)
        {
            double m = mag[i];
            if (m < 1e-7) { m = 1e-7; }
            double v = 20.0 * Math.Log10(m);
            if (v < -140.0) { v = -140.0; }
            db[i] = v;
        }
        return db;
    }

    // 瀑布图单行颜色映射（BGR 24bpp 行缓冲）：0=深蓝 → 青 → 绿 → 黄 → 红 → 1=白
    // gamma：感知色标。输入已是 dB（对数域），但 dB→颜色为线性均分，低电平层次被压缩；
    // gamma<1（如 0.5）把 t^gamma 后再配色，拉开噪声底附近的层次（对数色标观感）
    private static void ColorMap(double t, out byte r, out byte g, out byte b)
    {
        if (t < 0) { t = 0; }
        if (t > 1) { t = 1; }
        if (t < 0.30) { double k = t / 0.30; r = (byte)(k * 10); g = (byte)(k * 40); b = (byte)(60 + k * 130); }
        else if (t < 0.50) { double k = (t - 0.30) / 0.20; r = (byte)(10 + k * 0); g = (byte)(40 + k * 180); b = (byte)(190 - k * 60); }
        else if (t < 0.70) { double k = (t - 0.50) / 0.20; r = (byte)(10 + k * 100); g = (byte)(220 - k * 40); b = (byte)(130 - k * 100); }
        else if (t < 0.88) { double k = (t - 0.70) / 0.18; r = (byte)(110 + k * 145); g = (byte)(180 + k * 60); b = (byte)(30 + k * 0); }
        else { double k = (t - 0.88) / 0.12; r = (byte)(255); g = (byte)(240 + k * 15); b = (byte)(30 + k * 225); }
    }

    public static byte[] WaterfallRow(double[] db, double dbMin, double dbMax, double gamma)
    {
        int w = db.Length;
        byte[] row = new byte[w * 3];
        double range = dbMax - dbMin;
        if (range <= 0) { range = 1.0; }
        if (gamma <= 0) { gamma = 1.0; }
        for (int i = 0; i < w; i++)
        {
            double t = (db[i] - dbMin) / range;
            if (t < 0) { t = 0; }
            if (t > 1) { t = 1; }
            t = Math.Pow(t, gamma);
            byte r, g, b;
            ColorMap(t, out r, out g, out b);
            row[i * 3] = b;
            row[i * 3 + 1] = g;
            row[i * 3 + 2] = r;
        }
        return row;
    }

    // ===== 频谱仪批量计算（热路径，避免 PS 逐 bin 循环）=====

    // 填充数组并返回（峰值保持初始化用）
    public static double[] FillArray(double[] a, double v)
    {
        for (int i = 0; i < a.Length; i++) { a[i] = v; }
        return a;
    }

    // 克隆数组（平均轨迹初始化用）
    public static double[] CopyArray(double[] src)
    {
        double[] r = new double[src.Length];
        Array.Copy(src, r, src.Length);
        return r;
    }

    // 峰值保持：maxHold[i] = max(maxHold[i], db[i])
    public static void UpdateMaxHold(double[] db, double[] maxHold)
    {
        int n = Math.Min(db.Length, maxHold.Length);
        for (int i = 0; i < n; i++)
        {
            if (db[i] > maxHold[i]) { maxHold[i] = db[i]; }
        }
    }

    // 指数平均：avg[i] += alpha * (db[i] - avg[i])
    public static void UpdateEma(double[] db, double[] avg, double alpha)
    {
        int n = Math.Min(db.Length, avg.Length);
        for (int i = 0; i < n; i++)
        {
            avg[i] += alpha * (db[i] - avg[i]);
        }
    }

    // 频谱轨迹 → 屏幕点列（钳位到 [dbBottom, dbTop]，横向等分）。
    // 返回 PointF[] 供 Graphics.DrawLines 一次画完；PS 侧不再逐 bin 建 PointF
    public static System.Drawing.PointF[] SpectrumTracePoints(double[] data, float x0, float y0, float w, float h, float dbTop, float dbBottom)
    {
        int n = data.Length;
        System.Drawing.PointF[] pts = new System.Drawing.PointF[n];
        float range = dbTop - dbBottom;
        if (range <= 0.0f) { range = 1.0f; }
        float denom = (n > 1) ? (n - 1) : 1.0f;
        for (int k = 0; k < n; k++)
        {
            float v = (float)data[k];
            if (v > dbTop) { v = dbTop; }
            if (v < dbBottom) { v = dbBottom; }
            float xx = x0 + ((float)k / denom) * w;
            float yy = y0 + (dbTop - v) / range * h;
            pts[k] = new System.Drawing.PointF(xx, yy);
        }
        return pts;
    }

    // 抽稀版：bin 数远超像素宽时按列取 MAX（频谱显示惯例：保峰单点/列）。
    // GDI+ AA 折线成本 ∝ 线段数+墨量：4096 点亚像素间距 ~11ms/条；
    // min/max 之字形会引入整列斜连接线使墨量翻倍，故只取每列最大值
    public static System.Drawing.PointF[] SpectrumTracePointsDecimated(double[] data, int maxPoints, float x0, float y0, float w, float h, float dbTop, float dbBottom)
    {
        int n = data.Length;
        if (n <= maxPoints || maxPoints < 4)
        {
            return SpectrumTracePoints(data, x0, y0, w, h, dbTop, dbBottom);
        }
        int bs = (n + maxPoints - 1) / maxPoints;
        int buckets = (n + bs - 1) / bs;
        System.Drawing.PointF[] pts = new System.Drawing.PointF[buckets];
        float range = dbTop - dbBottom;
        if (range <= 0.0f) { range = 1.0f; }
        float denom = (n > 1) ? (n - 1) : 1.0f;
        for (int b = 0; b < buckets; b++)
        {
            int s = b * bs;
            int e = Math.Min(n, s + bs);
            float vmax = float.MinValue;
            for (int k = s; k < e; k++)
            {
                if ((float)data[k] > vmax) { vmax = (float)data[k]; }
            }
            if (vmax > dbTop) { vmax = dbTop; }
            if (vmax < dbBottom) { vmax = dbBottom; }
            float xx = x0 + ((float)s / denom) * w;
            float yy = y0 + (dbTop - vmax) / range * h;
            pts[b] = new System.Drawing.PointF(xx, yy);
        }
        return pts;
    }

    // 最大 bin 搜索（跳过前 skipBin 个，主频标注跳 DC）
    public static int MaxBin(double[] db, int skipBin)
    {
        int idx = (skipBin < db.Length) ? skipBin : 0;
        for (int i = idx; i < db.Length; i++)
        {
            if (db[i] > db[idx]) { idx = i; }
        }
        return idx;
    }
}
'@ -ReferencedAssemblies @('System.Drawing') -ErrorAction Stop
}

if (-not ('DwmDark' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class DwmDark
{
    [DllImport("dwmapi.dll")]
    public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int val, int size);

    // 深色标题栏：Win11 用属性 20，老版本 Win10 回落 19；失败静默保持系统默认
    public static void EnableDarkTitleBar(IntPtr hwnd, bool enabled)
    {
        try
        {
            int val = enabled ? 1 : 0;
            if (DwmSetWindowAttribute(hwnd, 20, ref val, 4) != 0)
            {
                DwmSetWindowAttribute(hwnd, 19, ref val, 4);
            }
        }
        catch
        {
        }
    }
}
'@ -ErrorAction Stop
}

# 滚动条是系统用 ScrollBar 类子窗口单独画的，完全不吃父控件的 BackColor，
# 深色主题下会在 RX/TX/发送框边上留一条刺眼的白边。唯一可控的办法是给这些
# 子窗口挂 uxtheme 的 DarkMode_Explorer（Win10 1809+ / Win11 官方暗色滚动条）。
if (-not ('UxTheme' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class UxTheme
{
    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
    public static extern int SetWindowTheme(IntPtr hwnd, string subAppName, string subIdList);

    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr GetWindowTheme(IntPtr hwnd);

    private delegate bool EnumChildProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, EnumChildProc proc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr hwnd, StringBuilder text, int max);

    // 直接给控件自身句柄挂暗色主题——WinForms 的滚动条是画在控件窗口上的
    // （EnumChildWindows 找不到独立的 ScrollBar 子窗口，实测就是空的），
    // 所以官方做法是对 TextBox/RichTextBox/ListBox 的 Handle 调用本方法。
    public static bool ApplyDark(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) { return false; }
        return SetWindowTheme(hwnd, "DarkMode_Explorer", null) == 0;
    }

    // 自检用：判断该窗口当前是否挂上了 uxtheme 主题。
    // 注意 GetWindowTheme 返回的是 HTHEME【句柄】不是字符串，
    // 拿它去 PtrToStringUni 解是不成立的（曾经这么干过，永远读出 "(none)"）。
    // 另外 GetWindowThemeClassName 在部分 Windows 版本的 uxtheme.dll 里
    // 根本没有导出，调用会抛「找不到导出例程」，所以不指望它读主题名。
    public static string ThemeOf(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) { return "(no-handle)"; }
        return (GetWindowTheme(hwnd) != IntPtr.Zero) ? "applied" : "(none)";
    }

    // 深色滚动条：递归找所有 ScrollBar 类后代并挂暗色主题。
    // 滚动条 HWND 是控件创建句柄时才生成的，所以要在窗体显示之后调用。
    public static int ApplyDarkScrollbars(IntPtr parent)
    {
        Holder h = new Holder();
        Walk(parent, h);
        return h.Count;
    }

    // 累加器用对象而不是 ref/out：C# 不允许在 lambda 体内引用 ref/out 参数，
    // 用 ref touched 会被 C#5 编译器直接拒绝（PS5.1 走 CodeDom，只支持 C#5）
    private class Holder
    {
        public int Count;
        public string Theme;
    }

    private static void Walk(IntPtr parent, Holder h)
    {
        EnumChildWindows(parent, delegate(IntPtr hwnd, IntPtr lParam) {
            StringBuilder sb = new StringBuilder(64);
            GetClassNameW(hwnd, sb, sb.Capacity);
            if (string.Equals(sb.ToString(), "ScrollBar", StringComparison.OrdinalIgnoreCase))
            {
                SetWindowTheme(hwnd, "DarkMode_Explorer", null);
                h.Count++;
            }
            Walk(hwnd, h);
            return true;
        }, IntPtr.Zero);
    }

    // 自检用：报告第一个 ScrollBar 后代当前挂的主题名（没挂返回 "(none)"）
    public static string FirstScrollbarTheme(IntPtr parent)
    {
        Holder h = new Holder();
        h.Theme = "(none)";
        Walk2(parent, h);
        return h.Theme;
    }

    private static void Walk2(IntPtr parent, Holder h)
    {
        if (h.Theme != "(none)") { return; }
        EnumChildWindows(parent, delegate(IntPtr hwnd, IntPtr lParam) {
            StringBuilder sb = new StringBuilder(64);
            GetClassNameW(hwnd, sb, sb.Capacity);
            if (string.Equals(sb.ToString(), "ScrollBar", StringComparison.OrdinalIgnoreCase))
            {
                IntPtr namePtr = GetWindowTheme(hwnd);
                h.Theme = (namePtr == IntPtr.Zero) ? "(none)" : Marshal.PtrToStringUni(namePtr);
                return false; // 找到即停
            }
            Walk2(hwnd, h);
            return true;
        }, IntPtr.Zero);
    }
}
'@ -ErrorAction Stop
}

# ---- 自绘控件：下拉箭头 / 复选框（放弃 uxtheme 后的替代方案）----
# 实测结论（logs/probe_darkdraw.png，screen 抓屏 + C:\Temp\v6probe 探针复验）：
#  ① DarkMode_Explorer 在浅色系统上完全失效，箭头/勾选框/滚动条仍是浅色；
#  ② WinForms 的 CheckBox【没有】DrawMode 属性，OwnerDraw 这条路走不通，
#     只能用子类重写 OnPaint；
#  ③ ComboBox 的 OnPaint【永远不会触发】（WM_PAINT 走原生窗口过程），深色箭头
#     必须在 WndProc 拦截 WM_PAINT、base 画完后再叠加；
#  ④ DropDownList 闭合区只有 CBS_OWNERDRAWFIXED 才吃得到（OnDrawItem），
#     且 DrawMode 必须在句柄创建前定死，见 DarkComboBox 构造器注释。
# UseDark=false 时完全走原生渲染（浅色主题不受影响）。
if (-not ('DarkComboBox' -as [type])) {
    Add-Type -ReferencedAssemblies @('System.Drawing', 'System.Windows.Forms') -TypeDefinition @'
using System;
using System.Drawing;
using System.Windows.Forms;

public class DarkComboBox : ComboBox
{
    // DrawMode 必须在构造器里一次性定死（此时句柄尚不存在，setter 只写托管字段，无重入风险）：
    // DropDownList 的闭合文字区是系统按 SystemColors.Window 主题硬画的，BackColor/ForeColor
    // 对它完全无效，只有 CBS_OWNERDRAWFIXED 才能把闭合区与下拉列表一起交给 OnDrawItem。
    // 【不要】在 OnHandleCreated 里再改 DrawMode——那时句柄已存在，setter 会走 RecreateHandle
    // 重入（销毁再创建正处于创建回调里的窗口），时序敏感：窗口最大化/高负载等交织下可能
    // 失败或半途吞异常，托管属性与原生窗口样式脱节，闭合区退回白底（线上实测回归过）。
    public bool UseDark = false;
    public Color ArrowBack = Color.FromArgb(48, 48, 48);
    public Color ArrowFore = Color.FromArgb(212, 212, 212);
    public Color BorderColor = Color.FromArgb(62, 62, 66);
    public Color HighlightBack = Color.FromArgb(14, 99, 156);
    public Color HighlightFore = Color.White;
    private static readonly int ArrowW = SystemInformation.VerticalScrollBarWidth + 1;
    // DropDownList 闭合态 OnDrawItem 报告的文字区（叠加层用它避开文字只补衬底）
    private Rectangle _itemBounds = Rectangle.Empty;

    public DarkComboBox()
    {
        this.DrawMode = DrawMode.OwnerDrawFixed;
    }

    // 可编辑（DropDown）的文字区是 ComboBox 内嵌的子 EDIT 控件：它的配色不靠 BackColor
    // 属性，而是父窗体在 WM_CTLCOLOREDIT 里返回的画刷/颜色。波特率框就靠这条路径上色。
    public Color EditBack = Color.FromArgb(45, 45, 48);
    public Color EditFore = Color.FromArgb(212, 212, 212);
    IntPtr _editBrush = IntPtr.Zero;
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern IntPtr CreateSolidBrush(int color);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern bool DeleteObject(IntPtr h);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern int SetTextColor(IntPtr hdc, int color);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern int SetBkColor(IntPtr hdc, int color);
    public void EnableDarkEdit(bool on, Color back, Color fore)
    {
        if (_editBrush != IntPtr.Zero) { DeleteObject(_editBrush); _editBrush = IntPtr.Zero; }
        if (on)
        {
            EditBack = back; EditFore = fore;
            _editBrush = CreateSolidBrush(back.R | (back.G << 8) | (back.B << 16));
        }
        this.Invalidate();
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x000F) // WM_PAINT：base 画完（含 OnDrawItem 文字）后再叠深色外框与箭头
        {
            base.WndProc(ref m);
            if (UseDark && this.IsHandleCreated && !this.DroppedDown) { PaintDarkChrome(); }
            return;
        }
        if (UseDark &&
            (m.Msg == 0x0133 || m.Msg == 0x0134 || m.Msg == 0x0138 || m.Msg == 0x2134 || m.Msg == 0x2138))
        {
            // 0x0133 = WM_CTLCOLOREDIT：DropDown 内嵌子 EDIT 的画刷
            // 0x0134 = WM_CTLCOLORLISTBOX：下拉列表空白区衬底——列表是组合框的子窗口，
            //          原生直发到本控件；反射变体 0x2134 在这条路径上根本到不了，
            //          此前只匹配反射号，弹层衬底一直走系统白（下拉白底闪现的根因）
            // 0x0138 = WM_CTLCOLORSTATIC：闭合区在禁用/擦除路径上的衬底
            // 画刷按需惰性创建：不依赖 EnableDarkEdit 先行（波形窗组合框没走那条路）
            if (_editBrush == IntPtr.Zero)
            {
                _editBrush = CreateSolidBrush(EditBack.R | (EditBack.G << 8) | (EditBack.B << 16));
            }
            SetTextColor(m.WParam, EditFore.R | (EditFore.G << 8) | (EditFore.B << 16));
            SetBkColor(m.WParam, EditBack.R | (EditBack.G << 8) | (EditBack.B << 16));
            m.Result = _editBrush;
            return;
        }
        base.WndProc(ref m);
    }

    // 叠加层：系统主题在 item 区四周留的衬里和边框是浅色的，按主题色补画。
    // 只动 item 区之外（边框带 + 箭头块），不碰文字。
    private void PaintDarkChrome()
    {
        using (Graphics g = this.CreateGraphics())
        {
            int w = this.ClientSize.Width, h = this.ClientSize.Height;
            using (SolidBrush back = new SolidBrush(this.BackColor))
            {
                if (this.DropDownStyle == ComboBoxStyle.DropDownList && !_itemBounds.IsEmpty)
                {
                    // 上/下/左三条衬里带 + 右侧到箭头块之间的区域
                    Rectangle ib = _itemBounds;
                    g.FillRectangle(back, new Rectangle(0, 0, w, ib.Top));
                    g.FillRectangle(back, new Rectangle(0, ib.Bottom, w, Math.Max(0, h - ib.Bottom)));
                    g.FillRectangle(back, new Rectangle(0, ib.Top, ib.X, ib.Height));
                    int rightStart = ib.Right;
                    int rightWidth = (w - ArrowW) - rightStart;
                    if (rightWidth > 0) { g.FillRectangle(back, new Rectangle(rightStart, ib.Top, rightWidth, ib.Height)); }
                }
            }
            Rectangle a = new Rectangle(w - ArrowW, 0, ArrowW, h);
            using (SolidBrush ab = new SolidBrush(ArrowBack)) g.FillRectangle(ab, a);
            using (Pen p = new Pen(BorderColor)) g.DrawRectangle(p, 0, 0, w - 1, h - 1);
            int cx = a.X + a.Width / 2, cy = a.Y + a.Height / 2, s = 4;
            using (SolidBrush b = new SolidBrush(ArrowFore))
                g.FillPolygon(b, new Point[]{
                    new Point(cx - s, cy - s / 2), new Point(cx + s, cy - s / 2), new Point(cx, cy + s / 2) });
        }
    }

    // 尺寸变化后旧 item 区坐标失效：清空，待下次 OnDrawItem 重新捕获。
    // 否则 PaintDarkChrome 用旧矩形补衬底，可能把新尺寸下的文字带一起擦掉（文字偶发消失）
    protected override void OnResize(EventArgs e)
    {
        _itemBounds = Rectangle.Empty;
        base.OnResize(e);
    }

    protected override void OnDrawItem(DrawItemEventArgs e)
    {
        if (e.Bounds.Width > 0 && e.Bounds.Height > 0 &&
            this.DropDownStyle == ComboBoxStyle.DropDownList && !this.DroppedDown)
        {
            _itemBounds = e.Bounds;
        }
        if (e.Bounds.Width <= 0 || e.Bounds.Height <= 0) { return; }
        bool isItem = (e.Index >= 0 && e.Index < this.Items.Count);
        bool selected = isItem && ((e.State & DrawItemState.Selected) == DrawItemState.Selected);
        Color back = selected ? HighlightBack : this.BackColor;
        Color fore = selected ? HighlightFore : this.ForeColor;
        string text = isItem ? GetItemText(this.Items[e.Index]) : (this.Text ?? "");
        using (SolidBrush b = new SolidBrush(back)) { e.Graphics.FillRectangle(b, e.Bounds); }
        Rectangle tb = new Rectangle(e.Bounds.X + 4, e.Bounds.Y, e.Bounds.Width - 6, e.Bounds.Height);
        TextRenderer.DrawText(e.Graphics, text, this.Font, tb, fore,
            TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
    }

    protected override void Dispose(bool disposing)
    {
        if (_editBrush != IntPtr.Zero) { DeleteObject(_editBrush); _editBrush = IntPtr.Zero; }
        base.Dispose(disposing);
    }
}

public class DarkCheckBox : CheckBox
{
    public bool UseDark = false;
    public Color BoxBorder = Color.FromArgb(160, 160, 160);
    public Color MarkColor = Color.FromArgb(212, 212, 212);

    // 自绘时文字画在 x=20，但 AutoSize 的 GetPreferredSize 只按原生布局（勾选框 ~15px +
    // 文字）算宽度——差出的 ~6px 会让文字尾部伸到控件外，被右边的下一个控件盖住
    // （实测：「RTS」的 S 被「自动保存日志」遮挡）。这里把宽度补回来。
    public override Size GetPreferredSize(Size proposedSize)
    {
        Size s = base.GetPreferredSize(proposedSize);
        return new Size(s.Width + 6, s.Height);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        if (!UseDark) { base.OnPaint(e); return; }
        Graphics g = e.Graphics;
        // BackColor=Transparent 时 WinForms 已把父控件底色铺好，不要再 Clear，
        // 否则会盖掉父底色出现色块；只有不透明底色才自己清。
        if (this.BackColor.A == 255) { g.Clear(this.BackColor); }
        Color border = this.Enabled ? BoxBorder : Color.Gray;
        Color mark = this.Enabled ? MarkColor : Color.Gray;
        Rectangle box = new Rectangle(0, (this.Height - 14) / 2, 14, 14);
        using (Pen p = new Pen(border)) g.DrawRectangle(p, box);
        if (this.Checked)
        {
            using (Pen p = new Pen(mark, 2))
                g.DrawLines(p, new Point[]{
                    new Point(box.X + 3, box.Y + 7), new Point(box.X + 6, box.Y + 10), new Point(box.X + 10, box.Y + 4) });
        }
        string txt = this.Text ?? "";
        int ty = (this.Height - TextRenderer.MeasureText(txt, this.Font).Height) / 2;
        TextRenderer.DrawText(g, txt, this.Font, new Point(20, ty), this.ForeColor);
        if (this.Focused)
        {
            using (Pen p = new Pen(Color.Gray)) g.DrawRectangle(p, new Rectangle(-1, -1, this.Width + 1, this.Height + 1));
        }
    }
}

// 日志框滚动条：uxtheme 暗色滚动条在浅色系统上无效，改走「去主题 + WM_CTLCOLORSCROLLBAR」
// 只把【滑道】刷成深色，保留原生滚动/命中测试。注意经典滚动条的滑块 thumb 和上下箭头
// 仍由系统 3D 画（吃不到 CTLSCROLLBAR），要全暗必须替换原生滚动条——RichTextBox 的
// 横向滚动条无公开 API 可定位，代价过高，故此处只压掉刺眼的白色滑道底色。
public class DarkRichTextBox : RichTextBox
{
    public bool UseDarkScroll = false;
    public Color ScrollBack = Color.FromArgb(30, 30, 30);
    IntPtr _brush = IntPtr.Zero;
    [System.Runtime.InteropServices.DllImport("uxtheme.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int SetWindowTheme(IntPtr hwnd, string subAppName, string subIdList);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern IntPtr CreateSolidBrush(int color);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern bool DeleteObject(IntPtr h);
    public void EnableDarkScroll(bool on, Color back)
    {
        UseDarkScroll = on;
        if (_brush != IntPtr.Zero) { DeleteObject(_brush); _brush = IntPtr.Zero; }
        if (on) { ScrollBack = back; _brush = CreateSolidBrush(back.R | (back.G << 8) | (back.B << 16)); }
        if (this.IsHandleCreated && on) { SetWindowTheme(this.Handle, "", ""); this.Invalidate(); }
    }
    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        if (UseDarkScroll) { SetWindowTheme(this.Handle, "", ""); }
    }
    protected override void WndProc(ref Message m)
    {
        if (UseDarkScroll && _brush != IntPtr.Zero && m.Msg == 0x0133) { m.Result = _brush; return; } // WM_CTLCOLORSCROLLBAR
        base.WndProc(ref m);
    }
    protected override void Dispose(bool disposing)
    {
        if (_brush != IntPtr.Zero) { DeleteObject(_brush); _brush = IntPtr.Zero; }
        base.Dispose(disposing);
    }
}

public class DarkTextBox : TextBox
{
    public bool UseDarkScroll = false;
    public Color ScrollBack = Color.FromArgb(30, 30, 30);
    IntPtr _brush = IntPtr.Zero;
    [System.Runtime.InteropServices.DllImport("uxtheme.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int SetWindowTheme(IntPtr hwnd, string subAppName, string subIdList);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern IntPtr CreateSolidBrush(int color);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    static extern bool DeleteObject(IntPtr h);
    public void EnableDarkScroll(bool on, Color back)
    {
        UseDarkScroll = on;
        if (_brush != IntPtr.Zero) { DeleteObject(_brush); _brush = IntPtr.Zero; }
        if (on) { ScrollBack = back; _brush = CreateSolidBrush(back.R | (back.G << 8) | (back.B << 16)); }
        if (this.IsHandleCreated && on) { SetWindowTheme(this.Handle, "", ""); this.Invalidate(); }
    }
    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        if (UseDarkScroll) { SetWindowTheme(this.Handle, "", ""); }
    }
    protected override void WndProc(ref Message m)
    {
        if (UseDarkScroll && _brush != IntPtr.Zero && m.Msg == 0x0133) { m.Result = _brush; return; }
        base.WndProc(ref m);
    }
    protected override void Dispose(bool disposing)
    {
        if (_brush != IntPtr.Zero) { DeleteObject(_brush); _brush = IntPtr.Zero; }
        base.Dispose(disposing);
    }
}
'@ -ErrorAction Stop
}

# ============ V6Native：日志查找栏用的原生滚动（行定位到视口中间） ============
if (-not ('V6Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class V6Native
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    // 演示截图用：PrintWindow 直接渲染窗口自身像素（PW_RENDERFULLCONTENT=2），
    // 不要求窗口在前台，也不会把屏幕上无关内容拍进去
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
}
'@ -ErrorAction Stop
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$script:serial_port = $null
$script:log_file_path = $null
$script:log_writer = $null
$script:runtime_log_path = $null
$script:connected = $false
$script:receive_timer = $null
$script:ui_flush_timer = $null
$script:status_timer = $null
# set_actions_expanded 在顶层就会先跑一次（默认收起板卡操作行），那时
# apply_theme 还没跑过，StrictMode 下读未定义变量直接抛，所以必须先预设。
$script:ui_is_dark = $false
$script:scrollbar_darkened = 0
$script:rx_decoder = $null
$script:rx_line_buffer = ''
# 发送历史（cmd 风格 ⬆/⬇ 回翻）、文件发送、积压统计与日志悬停互转状态
# （对应函数在 core\serial_io.ps1；这里先建好状态避免 StrictMode 未定义）
$script:send_history = New-Object 'System.Collections.Generic.List[string]'
$script:send_history_max = 200
$script:send_history_pos = -1
$script:send_history_draft = ''
$script:file_send_state = $null
$script:rx_backlog_last = [long]0
$script:rx_backlog_growth_ticks = 0
# 「已接收未显示」积压量（未成行文本 + 波形协议残尾）与最近收到字节的时刻（空闲落显用）
$script:rx_backlog_bytes = [long]0
$script:rx_last_byte_at = $null
$script:wave_overflow_note_last = [datetime]::MinValue
$script:log_hover_tip = $null
$script:log_hover_state = $null
# 日志查找栏（Ctrl+F）：文本搜索 + 时间戳定位；高亮范围记录 + 输入防抖计时器
$script:find_panel = $null
$script:find_box = $null
$script:find_label_count = $null
$script:find_highlight_ranges = New-Object 'System.Collections.Generic.List[object]'
$script:find_live_timer = $null
$script:message_entries = New-Object 'System.Collections.Generic.List[object]'
$script:pending_display_entries = New-Object 'System.Collections.Generic.List[object]'
$script:pending_log_blocks = New-Object 'System.Collections.Generic.List[string]'
$script:rx_bytes_total = [long]0
$script:rx_line_total = [long]0
$script:ui_skip_total = [long]0
# 应用版本（与 GitHub Release tag 对齐比较；master 上的开发版先标 1.1.0）
$script:app_version = '1.1.0'
$script:connected_port_name = ''
$script:connected_baud_rate = 0
$script:max_textbox_chars = 1200000
$script:max_pending_display_entries = 20000
$script:max_message_entries = 20000
$script:max_display_entries_per_tick = 1200

# ---------- V5 波形状态（协议: FireWater / JustFloat / RawData，按通道存储） ----------
$script:wave_enabled = $false
$script:wave_protocol = 'FireWater'
$script:wave_ch_values = New-Object 'System.Collections.Generic.List[object]'
$script:wave_frame_count = 0
$script:wave_channel_last = New-Object 'System.Collections.Generic.List[double]'
$script:wave_channel_checks = New-Object 'System.Collections.Generic.List[object]'
$script:wave_frame_total = [long]0
$script:wave_fail_total = [long]0
$script:wave_max_frames = 200000
$script:wave_window_frames = 1000
$script:wave_paused = $false
$script:wave_auto_y = $true
$script:wave_view_start = -1
$script:wave_drag_state = $null
$script:wave_slider_drag = $null
$script:wave_slider_hover = 'none'
$script:wave_slider_panel = $null
$script:wcombo_window = $null
# 波形窗口控件在 open_wave_window 时才创建；全局预置 $null，
# 否则 StrictMode 下未开窗时（控制通道/模拟数据）连判空读取都抛"未设置该变量"
$script:wbutton_sim = $null
$script:wbutton_pause = $null
$script:wbutton_clear = $null
$script:wbutton_csv = $null
$script:wbutton_allch = $null
$script:wbutton_auto = $null
$script:wbutton_copyfmt = $null
$script:wbutton_format = $null
$script:wbutton_record = $null
$script:wcheck_autoy = $null
$script:wcombo_protocol = $null
$script:wcombo_preset = $null
$script:wbutton_preset_save = $null
$script:wbutton_preset_del = $null
$script:wave_pause_badge = $null
$script:wave_water_panel = $null
$script:wave_splitter = $null
$script:wave_splitter_drag = $null
$script:wlabel_stats = $null
$script:wtext_ymin = $null
$script:wtext_ymax = $null
$script:wave_channel_names = New-Object 'System.Collections.Generic.List[string]'
$script:wave_crosshair = $null
$script:wave_filter_mode = '关'
$script:wave_filter_param = 16
$script:wave_filter_cache = @{}
$script:wave_plugins = New-Object 'System.Collections.Generic.List[object]'
$script:wave_y_min = -1.0
$script:wave_y_max = 1.0
$script:wave_form = $null
$script:wave_panel = $null
$script:wave_channel_flow = $null
$script:wave_channel_header = $null
$script:wave_paint_timer = $null
$script:wave_last_painted_frame = [long]0
$script:wave_fps_count = 0
$script:wave_fps_value = 0
$script:wave_fps_reset_at = [datetime]::MinValue
$script:wave_rx_decoder = $null
$script:wave_line_buffer = ''
$script:wave_jf_buffer = New-Object 'System.Collections.Generic.List[byte]'
$script:wave_hint_last_rx = [long]0
$script:wave_hint_last_frames = [long]0
$script:session_record_writer = $null
$script:session_record_path = $null
$script:record_stopwatch = $null
$script:control_listener = $null
$script:control_timer = $null
$script:hit_regex = $null
$script:hit_pattern_text = ''
$script:gui_busy = $false
$script:port_probe_script = @'
$result = @{}
try {
    $entities = Get-CimInstance -ClassName Win32_PnPEntity -Filter "PNPClass='Ports'" -ErrorAction Stop
    foreach ($entity in $entities) {
        if ($entity.Name -and $entity.Name -match '\(COM(\d+)\)') {
            $result[($Matches[0].Trim('(', ')'))] = [string]$entity.Name
        }
    }
}
catch {
}
Write-Output $result
'@
$script:port_probe_busy = $false
$script:port_probe_ps = $null
$script:port_probe_handle = $null
$script:port_probe_first_done = $false
$script:known_ports = @()
$script:port_watch_timer = $null
$script:saved_port_name = ''
$script:wave_number_regex = [System.Text.RegularExpressions.Regex]::new('^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$')
$script:wave_plot_left = 56
$script:wave_plot_top = 12
$script:wave_plot_right = 14
$script:wave_plot_bottom = 24
$script:wave_sim_timer = $null
$script:wave_sim_on = $false
$script:wave_sim_t = 0

# ---------- XY 相位图（IQ 李萨如/星座图）状态 ----------
# RIGOL 式多窗口：1~4 个 XY 视图，默认 2 个；pairs = 每窗口 X/Y 源通道（扁平 X0,Y0,X1,Y1,...）。
# 余晖累积层为每窗口一组乒乓位图（A 显示 / B 淡出中转）；控件本体在 ensure_wave_xy_section 建。
$script:wave_xy_on = $false
$script:wave_xy_count = 2
$script:wave_xy_pairs = @(0, 1, 2, 3, 4, 5, 6, 7)
$script:wave_xy_persist = '1s'
# 量程/分格按窗口独立（默认固定 ±8、8 分格，不做动态自适应；'自适应' 才回退自动拟合）
$script:wave_xy_spans = @('±8', '±8', '±8', '±8')
$script:wave_xy_divs_arr = @(8, 8, 8, 8)
$script:wave_xy_syncing = $false
$script:wave_xy_accum_a = @($null, $null, $null, $null)
$script:wave_xy_accum_b = @($null, $null, $null, $null)
$script:wave_xy_accum_wh = @($null, $null, $null, $null)
$script:wave_xy_last_abs = [long]0
$script:wave_xy_last_tick = [datetime]::MinValue
$script:wave_xy_map_stamp = @('', '', '', '')
$script:wave_xy_map_prev = @($null, $null, $null, $null)
$script:wave_xy_section = $null
$script:wave_xy_sep = $null
$script:wbutton_xy = $null
$script:wcombo_xy_count = $null
$script:wcombo_xy_persist = $null
$script:wcombo_xy_span_boxes = @($null, $null, $null, $null)
$script:wcombo_xy_divs_boxes = @($null, $null, $null, $null)
$script:wave_xy_groups = @($null, $null, $null, $null)
$script:wcombo_xy_xs = @($null, $null, $null, $null)
$script:wcombo_xy_ys = @($null, $null, $null, $null)

# ---------- V5 FireWater 波形解析与缓冲 ----------

# ---------- 主题常量：三层灰 + 蓝强调色（改一处全局生效） ----------
$script:theme = @{
    BgWindow = [System.Drawing.Color]::FromArgb(20, 24, 33)
    BgPanel = [System.Drawing.Color]::FromArgb(28, 33, 45)
    BgInput = [System.Drawing.Color]::FromArgb(38, 45, 58)
    Border = [System.Drawing.Color]::FromArgb(70, 82, 105)
    TextMain = [System.Drawing.Color]::FromArgb(225, 230, 240)
    TextDim = [System.Drawing.Color]::FromArgb(168, 178, 195)
    Accent = [System.Drawing.Color]::FromArgb(64, 132, 200)
    AccentHover = [System.Drawing.Color]::FromArgb(88, 156, 226)
    AccentDown = [System.Drawing.Color]::FromArgb(48, 108, 168)
    BtnFace = [System.Drawing.Color]::FromArgb(42, 50, 68)
    BtnHover = [System.Drawing.Color]::FromArgb(58, 68, 90)
    BtnDown = [System.Drawing.Color]::FromArgb(32, 38, 52)
    PlotBgTop = [System.Drawing.Color]::FromArgb(22, 27, 38)
    PlotBgBottom = [System.Drawing.Color]::FromArgb(16, 20, 29)
    PlotFrame = [System.Drawing.Color]::FromArgb(58, 68, 88)
}

# ---------- 主窗体主题：深/浅两套 token，apply_theme 按主题名填 $script:ui ----------
# 波形/频谱窗继续只读 $script:theme，两边不要互相污染。
#
# 【背景三级】区域边界不能只靠色差，要同时给三级底色 + 1px 分隔线：
#   L1 顶部配置面板 / L2 底部发送面板 / L3 日志区，三档亮度等距递进。
#   注意 FormBg 实际上看不见——panel_top / panel_bottom / split_logs 三者都
#   Dock 占满窗口宽度，窗体底色被完全盖住，所以分级必须挂在这三块上，
#   不要再指望 FormBg 参与视觉分层。
#
# 【深色】VS Code Dark+ 极简风（用户选定 2026-10-20，方案 B）：
#   中性灰阶 + 单一蓝强调（按钮 #0E639C，悬停 #1177BB）+ 细边框（#3E3E42）。
#   发送记录用 VS Code 蓝 #569CD6 与接收区分开。对比度：
#     TextMain  #D4D4D4 / LogBgRx #1E1E1E = 7.06:1
#     TextMain  #D4D4D4 / PanelBg  #252526 = 5.57:1
#     TextMain  #D4D4D4 / InputBg  #2D2D30 = 5.16:1
#     Accent    #0E639C 底 + 纯白 = 3.06:1（大按钮文字可接受阈值 3:1）
#
# 【禁用态】WinForms 禁用态 Flat 按钮文字恒为近黑，且与 BackColor 无关
#   #2A3244 底 -> #0E1016 字 = 0.32:1；底色提亮到 #384054 仍只有 0.33:1
#   所以主窗体一律不使用 Enabled=False，改「保持可点 + 静音配色 + 状态位守卫」，
#   见 set_main_button_enabled。
$script:ui_palettes = @{
    Dark = @{
        # —— 背景三级 ——
        FormBg         = [System.Drawing.Color]::FromArgb(20, 20, 20)    # #141414（基本不露出）
        PanelBg        = [System.Drawing.Color]::FromArgb(37, 37, 38)    # #252526 L1 顶部面板
        PanelBg2       = [System.Drawing.Color]::FromArgb(45, 45, 48)    # #2D2D30 L2 底部发送面板
        LogBgRx        = [System.Drawing.Color]::FromArgb(30, 30, 30)    # #1E1E1E L3 日志区
        LogBgTx        = [System.Drawing.Color]::FromArgb(27, 27, 27)    # #1B1B1B L3 日志区（比 RX 再暗半档）
        # 竖分隔条：取"比日志区亮半档、比顶部面板暗半档"的中间色，宽度压到 3px，
        # 让它融进背景而不是切成一条黑缝（用户反馈：拖动条要与背景融洽）
        SplitterBg     = [System.Drawing.Color]::FromArgb(42, 42, 42)    # #2A2A2A
        Separator      = [System.Drawing.Color]::FromArgb(62, 62, 66)    # #3E3E42 分隔线（VS Code 细边框色）
        # —— 前景 ——
        TextMain       = [System.Drawing.Color]::FromArgb(212, 212, 212)  # #D4D4D4
        TextDim        = [System.Drawing.Color]::FromArgb(157, 157, 157)  # #9D9D9D
        TextMuted      = [System.Drawing.Color]::FromArgb(128, 128, 128)  # #808080
        # —— 输入 ——
        InputBg        = [System.Drawing.Color]::FromArgb(45, 45, 48)    # #2D2D30
        InputEdge      = [System.Drawing.Color]::FromArgb(62, 62, 66)    # #3E3E42
        # —— 按钮 ——
        BtnFace        = [System.Drawing.Color]::FromArgb(45, 45, 48)    # #2D2D30
        BtnHover       = [System.Drawing.Color]::FromArgb(62, 62, 66)    # #3E3E42
        BtnDown        = [System.Drawing.Color]::FromArgb(37, 37, 38)    # #252526
        BtnMutedBg     = [System.Drawing.Color]::FromArgb(46, 46, 46)    # #2E2E2E
        BtnMutedEdge   = [System.Drawing.Color]::FromArgb(72, 72, 72)    # #484848
        Accent         = [System.Drawing.Color]::FromArgb(14, 99, 156)    # #0E639C VS Code 按钮蓝
        AccentHover    = [System.Drawing.Color]::FromArgb(17, 119, 187)   # #1177BB
        AccentDown     = [System.Drawing.Color]::FromArgb(10, 78, 126)    # #0A4E7E
        AccentMutedBg  = [System.Drawing.Color]::FromArgb(38, 52, 66)    # #263442
        AccentMutedEdg = [System.Drawing.Color]::FromArgb(62, 84, 106)   # #3E546A
        # —— 日志文字与语义色 ——
        LogTextRx      = [System.Drawing.Color]::FromArgb(212, 212, 212)  # #D4D4D4
        LogTextTx      = [System.Drawing.Color]::FromArgb(86, 156, 214)   # #569CD6 VS Code 蓝（发送记录）
        StatusBg       = [System.Drawing.Color]::FromArgb(30, 36, 48)     # #1E2430 静音深底——大面积亮蓝(#0E639C)与暗面板冲突；连接态仍用 OkText/ErrText 着色
        StatusFg       = [System.Drawing.Color]::FromArgb(154, 164, 178)  # #9AA4B2
        HitText        = [System.Drawing.Color]::FromArgb(244, 135, 113)  # #F48771
        OkText         = [System.Drawing.Color]::FromArgb(78, 201, 176)   # #4EC9B0
        WarnText       = [System.Drawing.Color]::FromArgb(204, 167, 0)    # #CCA700
        ErrText        = [System.Drawing.Color]::FromArgb(241, 76, 76)    # #F14C4C
        InfoText       = [System.Drawing.Color]::FromArgb(86, 156, 214)   # #569CD6
    }
    Light = @{
        # —— Fluent 浅色圆角风（用户选定 2026-10-20，方案 D）——
        # 白色卡片浮在浅灰底上；圆角由 WinForms 能力所限只能近似，靠留白+细分隔线补偿。
        FormBg         = [System.Drawing.Color]::FromArgb(243, 243, 243)  # #F3F3F3（基本不露出）
        PanelBg        = [System.Drawing.Color]::FromArgb(255, 255, 255)  # #FFFFFF L1 顶部面板（卡片）
        PanelBg2       = [System.Drawing.Color]::FromArgb(250, 250, 250)  # #FAFAFA L2 底部发送面板
        LogBgRx        = [System.Drawing.Color]::FromArgb(255, 255, 255)  # #FFFFFF L3 日志区
        LogBgTx        = [System.Drawing.Color]::FromArgb(250, 250, 250)  # #FAFAFA L3 日志区
        SplitterBg     = [System.Drawing.Color]::FromArgb(237, 237, 237)  # #EDEDED 与两侧白/浅灰都接得住
        Separator      = [System.Drawing.Color]::FromArgb(225, 225, 225)  # #E1E1E1
        # —— 前景 ——
        TextMain       = [System.Drawing.Color]::FromArgb(26, 26, 26)     # #1A1A1A
        TextDim        = [System.Drawing.Color]::FromArgb(97, 97, 97)     # #616161
        TextMuted      = [System.Drawing.Color]::FromArgb(117, 117, 117)  # #757575
        # —— 输入 ——
        InputBg        = [System.Drawing.Color]::FromArgb(255, 255, 255)  # #FFFFFF
        InputEdge      = [System.Drawing.Color]::FromArgb(209, 209, 209)  # #D1D1D1
        # —— 按钮 ——
        BtnFace        = [System.Drawing.Color]::FromArgb(255, 255, 255)  # #FFFFFF
        BtnHover       = [System.Drawing.Color]::FromArgb(245, 245, 245)  # #F5F5F5
        BtnDown        = [System.Drawing.Color]::FromArgb(235, 235, 235)  # #EBEBEB
        BtnMutedBg     = [System.Drawing.Color]::FromArgb(245, 245, 245)  # #F5F5F5
        BtnMutedEdge   = [System.Drawing.Color]::FromArgb(224, 224, 224)  # #E0E0E0
        Accent         = [System.Drawing.Color]::FromArgb(15, 108, 189)   # #0F6CBD Fluent 蓝
        AccentHover    = [System.Drawing.Color]::FromArgb(17, 94, 163)    # #115EA3
        AccentDown     = [System.Drawing.Color]::FromArgb(15, 84, 140)    # #0F548C
        AccentMutedBg  = [System.Drawing.Color]::FromArgb(232, 241, 250)  # #E8F1FA
        AccentMutedEdg = [System.Drawing.Color]::FromArgb(185, 207, 228)  # #B9CFE4
        # —— 日志文字与语义色 ——
        LogTextRx      = [System.Drawing.Color]::FromArgb(26, 26, 26)     # #1A1A1A
        LogTextTx      = [System.Drawing.Color]::FromArgb(15, 108, 189)   # #0F6CBD Fluent 蓝（发送记录）
        StatusBg       = [System.Drawing.Color]::FromArgb(234, 234, 234)  # #EAEAEA Fluent 状态条
        StatusFg       = [System.Drawing.Color]::FromArgb(26, 26, 26)     # #1A1A1A
        HitText        = [System.Drawing.Color]::FromArgb(196, 43, 28)    # #C42B1C
        OkText         = [System.Drawing.Color]::FromArgb(16, 124, 16)    # #107C10
        WarnText       = [System.Drawing.Color]::FromArgb(157, 93, 0)     # #9D5D00
        ErrText        = [System.Drawing.Color]::FromArgb(196, 43, 28)    # #C42B1C
        InfoText       = [System.Drawing.Color]::FromArgb(15, 108, 189)   # #0F6CBD
    }
}

# 必须在任何 append_text_box / update_status 调用前就位（StrictMode 下未定义变量首读即抛）
$script:ui = $script:ui_palettes['Dark']

# ---------- 场景预设：波特率+协议+滤波+视图 一键切换（wave_presets.txt，INI 风格） ----------
# ---------- 自测（-SelfTest）：协议解析 + 缓冲 + 离屏渲染，不弹界面 ----------
if ($SelfTest) {
    $failures = New-Object 'System.Collections.Generic.List[string]'
    function assert_wave_test {
        param([string]$name, [bool]$condition)
        if (-not $condition) {
            [void]$failures.Add($name)
        }
    }

    $parsed = try_parse_wave_frame -line '1.5,-2,3e2'
    assert_wave_test 'parse_3ch' ($null -ne $parsed -and $parsed.Count -eq 3 -and $parsed[0] -eq 1.5 -and $parsed[1] -eq -2 -and $parsed[2] -eq 300)
    $parsed = try_parse_wave_frame -line '  12 , -0.5 , 7.25e-1 '
    assert_wave_test 'parse_spaces' ($null -ne $parsed -and $parsed[2] -eq 0.725)
    assert_wave_test 'parse_single' ($null -ne (try_parse_wave_frame -line '42'))
    assert_wave_test 'reject_text' ($null -eq (try_parse_wave_frame -line 'spec:abc'))
    assert_wave_test 'reject_nan' ($null -eq (try_parse_wave_frame -line '1.0,NaN'))
    assert_wave_test 'reject_command' ($null -eq (try_parse_wave_frame -line '$H:1.0'))
    assert_wave_test 'reject_empty' ($null -eq (try_parse_wave_frame -line ''))
    $ansi_line = [string][char]27 + '[32m1.5' + [string][char]27 + '[0m,2.5'
    assert_wave_test 'parse_ansi_cleaned' ($null -ne (try_parse_wave_frame -line (sanitize_terminal_text -text $ansi_line)))

    for ($i = 0; $i -lt 250; $i++) {
        $sample = [double[]]@([Math]::Sin($i / 10.0), ($i % 7))
        append_wave_frame -values $sample
    }
    assert_wave_test 'buffer_total' ($script:wave_frame_total -eq 250)
    assert_wave_test 'buffer_channels' ($script:wave_ch_values.Count -eq 2)
    assert_wave_test 'buffer_last' ($script:wave_channel_last[1] -eq (249 % 7))

    # JustFloat：完整帧 + 分片到达自动同步
    $script:wave_protocol = 'JustFloat'
    reset_wave_buffers
    reset_wave_decoder
    $jf_frame = New-Object byte[] 16
    [BitConverter]::GetBytes([float]1.5).CopyTo($jf_frame, 0)
    [BitConverter]::GetBytes([float](-2.25)).CopyTo($jf_frame, 4)
    [BitConverter]::GetBytes([float]3.75).CopyTo($jf_frame, 8)
    $jf_tail = [byte[]](0, 0, 0x80, 0x7F)
    $jf_tail.CopyTo($jf_frame, 12)
    consume_wave_justfloat_bytes -bytes ([byte[]]@($jf_frame[0..7]))
    consume_wave_justfloat_bytes -bytes ([byte[]]@($jf_frame[8..15]))
    assert_wave_test 'justfloat_split' ($script:wave_frame_count -eq 1 -and $script:wave_ch_values[0][0] -eq 1.5 -and $script:wave_ch_values[1][0] -eq -2.25 -and $script:wave_ch_values[2][0] -eq 3.75)

    # RawData：字节流直接作为采样点
    $script:wave_protocol = 'RawData'
    reset_wave_buffers
    reset_wave_decoder
    consume_wave_rawdata_bytes -bytes ([byte[]](5, 250, 128))
    assert_wave_test 'rawdata_stream' ($script:wave_frame_count -eq 3 -and $script:wave_ch_values[0][2] -eq 128)
    $script:wave_protocol = 'FireWater'
    reset_wave_buffers
    reset_wave_decoder

    # 离屏渲染：FireWater 多通道 / 手动Y轴 / 通道覆盖 / RawData / 空数据
    $test_bitmap = New-Object System.Drawing.Bitmap(900, 500)
    $test_graphics = [System.Drawing.Graphics]::FromImage($test_bitmap)
    for ($i = 0; $i -lt 100; $i++) {
        append_wave_frame -values ([double[]]@([Math]::Sin($i / 8.0), (2.0 * [Math]::Cos($i / 5.0))))
    }
    draw_wave_graphics -graphics $test_graphics -width 900 -height 500
    $script:wave_auto_y = $false
    draw_wave_graphics -graphics $test_graphics -width 900 -height 500
    $script:wave_auto_y = $true
    draw_wave_graphics -graphics $test_graphics -width 900 -height 500 -enabled_override ([bool[]]@($true, $false))
    $script:wave_protocol = 'RawData'
    reset_wave_buffers
    consume_wave_rawdata_bytes -bytes ([byte[]]((1..200 | ForEach-Object { [byte](($_ * 3) % 256) })))
    draw_wave_graphics -graphics $test_graphics -width 900 -height 500
    reset_wave_buffers
    draw_wave_graphics -graphics $test_graphics -width 900 -height 500
    $script:wave_protocol = 'FireWater'
    $test_graphics.Dispose()
    $test_bitmap.Dispose()

    # XY 相位图：默认固定量程 ±8（8 分格）→ 映射恒定；自适应档才自动拟合；量程按窗口独立
    $script:wave_xy_on = $true
    $script:wave_xy_count = 2
    $script:wave_xy_pairs = @(0, 1, 2, 3, 4, 5, 6, 7)
    $script:wave_xy_persist = '1s'
    $script:wave_xy_spans = @('±8', '±8', '±8', '±8')
    $script:wave_xy_divs_arr = @(8, 8, 8, 8)
    $xy_bitmap = New-Object System.Drawing.Bitmap(900, 500)
    $xy_graphics = [System.Drawing.Graphics]::FromImage($xy_bitmap)
    $xy_map_f = get_wave_xy_mapping -x_ch 0 -y_ch 1 -plot_width 900 -plot_height 460 -slot 0
    assert_wave_test 'xy_fixed_default' ($xy_map_f.XMin -eq -8.0 -and $xy_map_f.XSpan -eq 16.0 -and $xy_map_f.YMin -eq -8.0 -and $xy_map_f.XStep -eq 2.0)
    $script:wave_xy_spans[1] = '自适应'
    $xy_map_g = get_wave_xy_mapping -x_ch 0 -y_ch 1 -plot_width 460 -plot_height 460 -slot 1
    assert_wave_test 'xy_span_per_slot' ($xy_map_f.XSpan -eq 16.0 -and $xy_map_g.XSpan -ne 16.0)
    $xy_map_a = get_wave_xy_mapping -x_ch 0 -y_ch 1 -plot_width 460 -plot_height 460 -slot 1
    assert_wave_test 'xy_map_aspect' ([Math]::Abs($xy_map_a.XSpan - $xy_map_a.YSpan) -lt 0.000001)
    $xy_map_b = get_wave_xy_mapping -x_ch 0 -y_ch 1 -plot_width 460 -plot_height 460 -slot 1
    assert_wave_test 'xy_map_stable' ($xy_map_a.XMin -eq $xy_map_b.XMin -and $xy_map_a.K -eq $xy_map_b.K)
    $script:wave_xy_spans = @('±8', '±8', '±8', '±8')
    $xy_grid = get_wave_xy_grid -plot_width 900 -plot_height 460
    assert_wave_test 'xy_grid_two' ($xy_grid.Count -eq 2 -and $xy_grid.Cols -eq 2 -and $xy_grid.CellW -eq $xy_grid.CellH -and (($xy_grid.CellW * 2 + $xy_grid.Gap) -le 900))
    $xy_cell1 = get_wave_xy_cell_rect -index 1 -plot_width 900 -plot_height 460
    assert_wave_test 'xy_cell_rect' ($xy_cell1.X -eq ($xy_grid.OffX + $xy_grid.CellW + $xy_grid.Gap) -and $xy_cell1.W -eq $xy_grid.CellW -and $xy_cell1.H -eq $xy_grid.CellW)
    $script:wave_xy_count = 3
    assert_wave_test 'xy_count_snap' ((get_wave_xy_count) -eq 2)
    $script:wave_xy_count = 2
    draw_wave_xy_graphics -graphics $xy_graphics -width 900 -height 500 -direct
    update_wave_xy_accum -now ([datetime]::Now) -plot_width 900 -plot_height 460
    for ($i = 0; $i -lt 30; $i++) {
        append_wave_frame -values ([double[]]@([Math]::Cos($i / 12.0), [Math]::Sin($i / 12.0)))
    }
    update_wave_xy_accum -now ([datetime]::Now).AddMilliseconds(66) -plot_width 900 -plot_height 460
    draw_wave_xy_graphics -graphics $xy_graphics -width 900 -height 500
    assert_wave_test 'xy_accum_alive' ($null -ne $script:wave_xy_accum_a[0])
    assert_wave_test 'xy_last_abs' ($script:wave_xy_last_abs -eq $script:wave_frame_total)
    assert_wave_test 'xy_pair_default' ((get_wave_xy_pair -slot 0).Y -eq 1 -and (get_wave_xy_pair -slot 1).X -eq 1)
    $script:wave_xy_on = $false
    dispose_wave_xy_bitmaps
    $xy_graphics.Dispose()
    $xy_bitmap.Dispose()

    # HardFault 解码
    $fault = test_fault_dump_line -text 'CFSR:0x00008200 HFSR:0x40000000 MMFAR:0x00000004 BFAR:0x00000004'
    assert_wave_test 'fault_decode' ($null -ne $fault -and $fault.decoded -match 'PRECISERR' -and $fault.decoded -match 'FORCED' -and $fault.decoded -match '0x00000004')
    assert_wave_test 'fault_none' ($null -eq (test_fault_dump_line -text 'hello world'))

    # 触发引擎：命中 → 前后文分段落盘
    $trigger_config = Join-Path $env:TEMP ('trg_{0}.json' -f (Get-Date -Format 'HHmmssfff'))
    $events_dir = Join-Path $env:TEMP ('trg_events_{0}' -f (Get-Date -Format 'HHmmssfff'))
    [System.IO.File]::WriteAllText($trigger_config, '[{"name":"test_hit","pattern":"panic","before_lines":3,"after_lines":2}]', [System.Text.UTF8Encoding]::new($false))
    $trg = new_trigger_engine -config_file $trigger_config -events_dir $events_dir
    foreach ($feed_line in @('a1', 'a2', 'a3', 'boot ok', 'Kernel panic now', 'after1', 'after2', 'tail')) {
        [void](feed_trigger_line -engine $trg -line $feed_line -ms 0)
    }
    $event_files = @(Get-ChildItem -LiteralPath $events_dir -Filter 'test_hit_*.txt' -ErrorAction SilentlyContinue)
    $trigger_content = ''
    if ($event_files.Count -gt 0) {
        $trigger_content = [System.IO.File]::ReadAllText($event_files[0].FullName)
    }
    assert_wave_test 'trigger_segment' ($event_files.Count -eq 1 -and $trigger_content -match 'a3' -and $trigger_content -match 'panic' -and $trigger_content -match 'after2' -and $trigger_content -notmatch 'tail' -and $trigger_content -notmatch 'a1')
    Remove-Item -LiteralPath $trigger_config -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $events_dir -Recurse -Force -ErrorAction SilentlyContinue

    # 期望测试引擎（假读函数）
    $script:fake_read_index = 0
    $fake_read = {
        param([int]$timeout_ms)
        $script:fake_read_index++
        if ($script:fake_read_index -eq 1) {
            return 'U-Boot 2020 ok'
        }
        return 'nope'
    }
    $expect_result = invoke_expect_steps -steps @(@{ name = 'uboot'; send = 'version'; expect = 'U-Boot \d{4}' }, @{ name = 'fail_step'; send = 'x'; expect = 'WONT_MATCH' }) -send_func { param($text, [bool]$as_hex, $newline_mode) } -read_func $fake_read
    assert_wave_test 'expect_engine' ($expect_result.total -eq 2 -and $expect_result.fail -eq 1 -and $expect_result.steps[0].pass -and -not $expect_result.steps[1].pass)

    # 命中高亮正则：设置 → 匹配 → 清除
    set_hit_pattern -pattern 'panic|HardFault'
    assert_wave_test 'hit_pattern_set' ($null -ne $script:hit_regex -and $script:hit_regex.IsMatch('Kernel panic - abc') -and $script:hit_regex.IsMatch('HARDFAULT dump'))
    assert_wave_test 'hit_pattern_miss' (-not $script:hit_regex.IsMatch('normal boot'))
    set_hit_pattern -pattern ''
    assert_wave_test 'hit_pattern_clear' ($null -eq $script:hit_regex -and $script:hit_pattern_text -eq '')

    # 流式命令执行：正常命令与失败命令
    $stream_ok = invoke_stream_command -command 'cmd /c echo hello_stream'
    assert_wave_test 'stream_command_ok' ($stream_ok.exit_code -eq 0 -and @($stream_ok.lines | Where-Object { $_ -match 'hello_stream' }).Count -eq 1)
    $stream_fail = invoke_stream_command -command 'cmd /c exit 42'
    assert_wave_test 'stream_command_exit' ($stream_fail.exit_code -eq 42)

    # 帮助命令与未知命令
    $help_resp = handle_control_request -request_line '{"cmd":"help"}'
    assert_wave_test 'control_help' ($help_resp.ok -and $help_resp.commands.flash -and $help_resp.commands.reset)
    $bad_resp = handle_control_request -request_line '{"cmd":"no_such"}'
    assert_wave_test 'control_unknown' (-not $bad_resp.ok)

    # 串口显示文本与 COM 名解析
    assert_wave_test 'port_name_from_display_desc' ((get_port_name_from_display -text 'USB-SERIAL CH340 (COM6)') -eq 'COM6')
    assert_wave_test 'port_name_from_display_plain' ((get_port_name_from_display -text 'COM3') -eq 'COM3')
    assert_wave_test 'port_name_from_display_dash' ((get_port_name_from_display -text 'COM10 — USB Serial Port') -eq 'COM10')
    assert_wave_test 'port_name_from_display_empty' ((get_port_name_from_display -text '') -eq '')

    # 显示文本生成：去重复 COM 尾巴、空描述回落
    assert_wave_test 'port_display_with_desc' ((get_port_display_text -port_name 'COM6' -description 'USB-SERIAL CH340 (COM6)') -eq 'COM6 — USB-SERIAL CH340')
    assert_wave_test 'port_display_no_desc' ((get_port_display_text -port_name 'COM7' -description '') -eq 'COM7')
    assert_wave_test 'port_display_system' ((get_port_display_text -port_name 'COM1' -description '通信端口 (COM1)') -eq 'COM1 — 通信端口')

    # 新端口检测：首刷只建基线，之后才报告新增
    assert_wave_test 'detect_new_first_done' (@(detect_new_ports -current_names @('COM6') -previous_names @() -first_done $false).Count -eq 0)
    assert_wave_test 'detect_new_added' ((@(detect_new_ports -current_names @('COM6', 'COM7') -previous_names @('COM6') -first_done $true) -join ',') -eq 'COM7')
    assert_wave_test 'detect_new_none' (@(detect_new_ports -current_names @('COM6') -previous_names @('COM6') -first_done $true).Count -eq 0)

    # WMI 设备描述查询：返回 hashtable 且不抛异常
    $device_map = get_serial_device_map_sync
    assert_wave_test 'device_map_type' ($device_map -is [hashtable])

    # GUI 设置持久化往返（重定向到临时文件 + 假控件）
    function get_gui_settings_path { return (Join-Path $env:TEMP ('gui_settings_selftest_{0}.txt' -f $PID)) }
    $checkbox_autolink = New-Object DarkCheckBox
    $checkbox_autolink.Checked = $true
    $combobox_baud = New-Object DarkComboBox
    $combobox_baud.Text = '115200'
    $combobox_rx_format = New-Object DarkComboBox
    [void]$combobox_rx_format.Items.AddRange(@('ASCII', 'HEX'))
    $combobox_rx_format.SelectedItem = 'ASCII'
    $combobox_tx_format = New-Object DarkComboBox
    [void]$combobox_tx_format.Items.AddRange(@('ASCII', 'HEX'))
    $combobox_tx_format.SelectedItem = 'ASCII'
    $combobox_port = New-Object DarkComboBox
    [void]$combobox_port.Items.Add('COM8 — USB-SERIAL CH340')
    $combobox_port.SelectedIndex = 0

    save_gui_setting -key 'auto_link' -value '0'
    $checkbox_autolink.Checked = $true
    load_gui_settings
    assert_wave_test 'gui_settings_off' (-not $checkbox_autolink.Checked)
    save_gui_setting -key 'auto_link' -value '1'
    $checkbox_autolink.Checked = $false
    load_gui_settings
    assert_wave_test 'gui_settings_on' ($checkbox_autolink.Checked)

    # 会话参数保存 → 加载恢复（波特率/端口/收发格式）
    $combobox_baud.Text = '9600'
    $combobox_rx_format.SelectedItem = 'HEX'
    $combobox_tx_format.SelectedItem = 'HEX'
    save_gui_session
    $combobox_baud.Text = '115200'
    $combobox_rx_format.SelectedItem = 'ASCII'
    $combobox_tx_format.SelectedItem = 'ASCII'
    $script:saved_port_name = ''
    load_gui_settings
    assert_wave_test 'gui_session_baud' ($combobox_baud.Text -eq '9600')
    assert_wave_test 'gui_session_formats' ($combobox_rx_format.SelectedItem -eq 'HEX' -and $combobox_tx_format.SelectedItem -eq 'HEX')
    assert_wave_test 'gui_session_port' ($script:saved_port_name -eq 'COM8')

    # 通道名持久化：保存 → 清空 → 加载恢复
    save_gui_setting -key 'channel_names' -value '电压,电流'
    $script:wave_channel_names = New-Object 'System.Collections.Generic.List[string]'
    load_gui_settings
    assert_wave_test 'chname_persist' ($script:wave_channel_names.Count -eq 2 -and $script:wave_channel_names[0] -eq '电压' -and $script:wave_channel_names[1] -eq '电流')
    Remove-Item -LiteralPath (get_gui_settings_path) -Force -ErrorAction SilentlyContinue

    # 通道显示名：默认 CHn，设置后用自定义名
    $script:wave_channel_names = New-Object 'System.Collections.Generic.List[string]'
    assert_wave_test 'chname_default' ((get_wave_channel_display_name -index 0) -eq 'CH0')
    [void]$script:wave_channel_names.Add('温度')
    assert_wave_test 'chname_custom' ((get_wave_channel_display_name -index 0) -eq '温度')
    assert_wave_test 'chname_default_other' ((get_wave_channel_display_name -index 1) -eq 'CH1')

    # 波形视图窗口：跟随 / 平移 / 端点拖动 / 宽度调整
    $script:wave_window_frames = 1000
    $script:wave_frame_count = 20000
    $script:wave_view_start = -1
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_follow' ($view.Follow -and $view.Start -eq 19000 -and $view.End -eq 20000)
    set_wave_view_pan -delta_frames (-5000) -total $script:wave_frame_count
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_pan' (-not $view.Follow -and $view.Start -eq 14000 -and $view.End -eq 15000)
    set_wave_view_pan -delta_frames 99999 -total $script:wave_frame_count
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_pan_return_follow' ($view.Follow -and $view.Start -eq 19000)
    # 从最右端（19000）持续左移，验证 clamp 到 0
    set_wave_view_pan -delta_frames (-8000) -total $script:wave_frame_count
    assert_wave_test 'view_pan_from_follow' ((get_wave_view_range -total 20000).Start -eq 11000)
    set_wave_view_pan -delta_frames (-11000) -total $script:wave_frame_count
    assert_wave_test 'view_pan_clamp_left' ((get_wave_view_range -total 20000).Start -eq 0)
    # 拖左点：起点 -2000，终点不变 → 宽度变大、中点保持在两点正中
    $script:wave_view_start = 14000
    set_wave_view_window -new_start 12000 -new_end 15000 -total $script:wave_frame_count
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_window_endpoints' ($view.Start -eq 12000 -and $view.End -eq 15000 -and $script:wave_window_frames -eq 3000)
    # 拖右点贴到最右 → 实时跟随
    set_wave_view_window -new_start 12000 -new_end 20000 -total $script:wave_frame_count
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_window_to_follow' ($view.Follow)
    # 宽度调整：历史浏览时保持窗口中心
    $script:wave_view_start = 10000
    $script:wave_window_frames = 2000
    set_wave_view_width -new_width 4000 -total $script:wave_frame_count
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_width_keep_center' ($view.Start -eq 9000 -and $view.End -eq 13000 -and $script:wave_window_frames -eq 4000)
    # 缓冲过小 → 强制跟随
    $script:wave_view_start = 5
    $view = get_wave_view_range -total 300
    assert_wave_test 'view_small_buffer_follow' ($view.Follow)
    # AI 格式文档生成：三协议各自包含关键要素
    $fw_doc = get_ai_format_doc -protocol 'FireWater'
    assert_wave_test 'ai_doc_firewater' ($fw_doc -match 'FireWater' -and $fw_doc -match 'ch0' -and $fw_doc -match 'printf')
    $jf_doc = get_ai_format_doc -protocol 'JustFloat'
    assert_wave_test 'ai_doc_justfloat' ($jf_doc -match 'float32' -and $jf_doc -match '0x80' -and $jf_doc -match 'Serial\.write')
    $rd_doc = get_ai_format_doc -protocol 'RawData'
    assert_wave_test 'ai_doc_rawdata' ($rd_doc -match '0~255' -and $rd_doc -match '单通道')
    $unknown_doc = get_ai_format_doc -protocol 'Nope'
    assert_wave_test 'ai_doc_default' ($unknown_doc -match 'FireWater')

    # HEX 发送换行拼接：byte[]+数组曾抛 op_Addition（CRLF 两元素），统一 object[] 拼接
    foreach ($mode_pair in @(@('CR', 1), @('LF', 1), @('CRLF', 2), @('NONE', 0))) {
        $nb = get_newline_bytes_for_mode -mode $mode_pair[0]
        assert_wave_test ('newline_bytes_' + $mode_pair[0].ToLower()) ($nb -is [byte[]] -and $nb.Count -eq $mode_pair[1])
    }
    $hex_payload = try_parse_hex_text -text 'AA'
    $combined = @([object[]]$hex_payload + [object[]](get_newline_bytes_for_mode -mode 'CRLF'))
    assert_wave_test 'hex_newline_concat' ($combined.Count -eq 3 -and $combined[0] -eq 0xAA -and $combined[1] -eq 13 -and $combined[2] -eq 10)

    # CH340 优先选取：多新端口选 CH340，无 CH340 取第一项
    assert_wave_test 'pick_auto_ch340' ((pick_auto_connect_port -new_ports @('COM6', 'COM8') -device_map @{ 'COM6' = '蓝牙链接上的标准串行 (COM6)'; 'COM8' = 'USB-SERIAL CH340 (COM8)' }) -eq 'COM8')
    assert_wave_test 'pick_auto_first' ((pick_auto_connect_port -new_ports @('COM3', 'COM4') -device_map @{}) -eq 'COM3')
    assert_wave_test 'ch340_match' (test_is_ch340_description -description 'USB-SERIAL CH340 (COM8)' -and (test_is_ch340_description -description 'USB-SERIAL CH341A (COM5)'))
    assert_wave_test 'ch340_no_match' (-not (test_is_ch340_description -description '蓝牙链接上的标准串行 (COM6)'))

    # 滑块拖点越过对侧：被拖侧钳位到锚点 ±50，锚点侧钉住不动
    set_wave_view_window -new_start 19900 -new_end 15000 -total $script:wave_frame_count -anchor 'start'
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_window_cross_start' ($view.End -eq 15000 -and ($view.End - $view.Start) -eq 50)
    set_wave_view_window -new_start 5000 -new_end 4900 -total $script:wave_frame_count -anchor 'end'
    $view = get_wave_view_range -total $script:wave_frame_count
    assert_wave_test 'view_window_cross_end' ($view.Start -eq 5000 -and ($view.End - $view.Start) -eq 50)

    # 滑块命中判定：轨道 [14, 786]，follow 窗口在最右端（start≈747.5/mid≈766.75/end=786）
    $script:wave_frame_count = 20000
    $script:wave_window_frames = 1000
    $script:wave_view_start = -1
    assert_wave_test 'slider_hit_end' ((get_wave_slider_hit -x 786 -width 800 -total 20000) -eq 'end')
    assert_wave_test 'slider_hit_start_follow' ((get_wave_slider_hit -x 747 -width 800 -total 20000) -eq 'start')
    assert_wave_test 'slider_hit_none' ((get_wave_slider_hit -x 400 -width 800 -total 20000) -eq 'none')
    $script:wave_view_start = 0
    assert_wave_test 'slider_hit_start_pan' ((get_wave_slider_hit -x 14 -width 800 -total 20000) -eq 'start')

    # WaveDsp 滤波正确性：滑动平均手算对账、零相位低通常数保持
    $flt_in = [double[]](1.0, 2.0, 3.0, 4.0, 5.0)
    $flt_ma = [WaveDsp]::MovingAverage($flt_in, 2)
    assert_wave_test 'dsp_moving_avg' ($flt_ma[0] -eq 1.0 -and $flt_ma[1] -eq 1.5 -and $flt_ma[2] -eq 2.5 -and $flt_ma[4] -eq 4.5)
    $flt_const = [WaveDsp]::ZeroPhaseLowPass([double[]](3.3, 3.3, 3.3, 3.3, 3.3), 0.2)
    assert_wave_test 'dsp_zp_const' ([Math]::Abs($flt_const[2] - 3.3) -lt 0.0001)
    # 零相位：正弦峰值位置不偏移（对称双向滤波）
    $sine_in = New-Object double[] 256
    for ($i = 0; $i -lt 256; $i++) { $sine_in[$i] = [Math]::Sin(2 * [Math]::PI * $i / 64) }
    $in_peak = 0
    for ($i = 1; $i -lt 256; $i++) { if ($sine_in[$i] -gt $sine_in[$in_peak]) { $in_peak = $i } }
    $sine_flt = [WaveDsp]::ZeroPhaseLowPass($sine_in, 0.5)
    $out_peak = 0
    for ($i = 1; $i -lt 256; $i++) { if ($sine_flt[$i] -gt $sine_flt[$out_peak]) { $out_peak = $i } }
    assert_wave_test 'dsp_zp_phase' ([Math]::Abs($in_peak - $out_peak) -le 2)

    # FFT 频率对账：Fs=1024，正弦 f=128Hz（第 128 bin）应在该处出现峰
    $fft_in = New-Object double[] 1024
    for ($i = 0; $i -lt 1024; $i++) { $fft_in[$i] = [Math]::Sin(2 * [Math]::PI * 128 * $i / 1024) }
    $fft_used = 0
    $fft_mag = [WaveDsp]::MagnitudeSpectrum($fft_in, [ref]$fft_used)
    $fft_peak = 1
    for ($k = 1; $k -lt $fft_mag.Count; $k++) { if ($fft_mag[$k] -gt $fft_mag[$fft_peak]) { $fft_peak = $k } }
    assert_wave_test 'dsp_fft_bin' ($fft_used -eq 1024 -and $fft_peak -ge 126 -and $fft_peak -le 130 -and $fft_mag.Count -eq 512)

    # 插件注册：注册/覆盖/查找
    $script:wave_plugins.Clear()
    $test_panel = { }
    $reg1 = Register-WavePlugin -Name '测试插件' -OpenPanel $test_panel
    $reg2 = Register-WavePlugin -Name '测试插件' -OpenPanel $test_panel
    assert_wave_test 'plugin_register' ($reg1 -and $reg2 -and $script:wave_plugins.Count -eq 1 -and $script:wave_plugins[0].Name -eq '测试插件')

    # 场景预设文件往返（写临时文件，不动用户真实 wave_presets.txt）
    $preset_tmp = Join-Path $env:TEMP ('wave_presets_selftest_{0}.txt' -f $PID)
    $tp = [pscustomobject]@{ Name = '自测预设'; Baud = '115200'; Protocol = 'JustFloat'; Filter = '滑动平均'; FilterParam = '8'; AutoY = $false; YMin = '0'; YMax = '255'; Window = '500' }
    save_wave_presets -presets @($tp) -path $preset_tmp
    $preset_round = @(load_wave_presets -path $preset_tmp)
    assert_wave_test 'preset_roundtrip' ($preset_round.Count -eq 1 -and $preset_round[0].Name -eq '自测预设' -and $preset_round[0].Baud -eq '115200' -and $preset_round[0].Protocol -eq 'JustFloat' -and $preset_round[0].Filter -eq '滑动平均' -and $preset_round[0].AutoY -eq $false -and $preset_round[0].YMin -eq '0' -and $preset_round[0].Window -eq '500')
    Remove-Item -LiteralPath $preset_tmp -ErrorAction SilentlyContinue

    # 发送历史 ⬆/⬇ 回翻（cmd 风格）：连续去重、栈深上限、到底不回绕、⬇ 走完恢复草稿
    $script:send_history = New-Object 'System.Collections.Generic.List[string]'
    $script:send_history_pos = -1
    $script:send_history_draft = ''
    $textbox_send = New-Object DarkTextBox
    $textbox_send.Text = 'editing'
    push_send_history -text 'ATZ'
    push_send_history -text 'ATZ'
    push_send_history -text 'AT+CGMR'
    assert_wave_test 'hist_dedupe' ($script:send_history.Count -eq 2)
    [void](recall_send_history -direction -1)
    assert_wave_test 'hist_up_newest' ($textbox_send.Text -eq 'AT+CGMR' -and $script:send_history_pos -eq 1)
    [void](recall_send_history -direction -1)
    assert_wave_test 'hist_up_oldest' ($textbox_send.Text -eq 'ATZ' -and $script:send_history_pos -eq 0)
    [void](recall_send_history -direction -1)
    assert_wave_test 'hist_up_clamp' ($textbox_send.Text -eq 'ATZ' -and $script:send_history_pos -eq 0)
    [void](recall_send_history -direction 1)
    assert_wave_test 'hist_down' ($textbox_send.Text -eq 'AT+CGMR' -and $script:send_history_pos -eq 1)
    [void](recall_send_history -direction 1)
    assert_wave_test 'hist_draft_restore' ($textbox_send.Text -eq 'editing' -and $script:send_history_pos -eq -1)
    for ($hi = 0; $hi -lt 210; $hi++) { push_send_history -text ('CMD{0}' -f $hi) }
    assert_wave_test 'hist_cap' ($script:send_history.Count -eq $script:send_history_max -and $script:send_history[0] -eq 'CMD10')

    # 日志行悬停格式互转：HEX→文本/十进制/float32，文本→HEX，短词不做 hex 误判
    $hover_hex = get_line_alt_format_tooltip -line '48 65 6C 6C 6F'
    assert_wave_test 'hover_hex_decode' ($hover_hex -match 'Hello' -and $hover_hex -match '十进制: 72 101 108 108 111')
    $hover_text = get_line_alt_format_tooltip -line 'Hello'
    assert_wave_test 'hover_text_encode' ($hover_text -match '^HEX: 48 65 6C 6C 6F')
    $hover_word = get_line_alt_format_tooltip -line 'OK'
    assert_wave_test 'hover_short_word' ($hover_word -match '^HEX:')
    $hover_float = get_line_alt_format_tooltip -line '00 00 80 3F'
    assert_wave_test 'hover_float32' ($hover_float -match 'float32\(小端\): 1')
    assert_wave_test 'hover_empty' ((get_line_alt_format_tooltip -line '  ') -eq '')

    # 缓冲积压口径：三个未解析缓冲之和（JustFloat 残尾 + 行缓冲）
    $script:wave_line_buffer = '12.5,'
    $script:wave_jf_buffer.Clear()
    [void]$script:wave_jf_buffer.Add(0x00)
    $backlog = [long]$script:rx_line_buffer.Length + [long]$script:wave_jf_buffer.Count + [long]$script:wave_line_buffer.Length
    assert_wave_test 'backlog_sum' ($backlog -eq 6)
    $script:wave_line_buffer = ''
    $script:wave_jf_buffer.Clear()

    # 状态条积压显示：出现「积压 N B」、持续增长变 warn 色、排空后消失
    $label_status = New-Object System.Windows.Forms.Label
    $checkbox_auto_save = New-Object DarkCheckBox
    $button_send_file = $null
    $script:connected = $true
    $script:connected_port_name = 'COM99'
    $script:connected_baud_rate = 1500000
    $script:wave_enabled = $true
    $script:rx_bytes_total = [long]2048
    $script:rx_line_total = [long]3
    $script:rx_line_buffer = ''
    $script:wave_line_buffer = ''
    $script:rx_backlog_last = [long]0
    $script:rx_backlog_growth_ticks = 0
    update_connected_status
    assert_wave_test 'status_clean' ($label_status.Text -match 'COM99' -and $label_status.Text -match '积压 0 B' -and $script:rx_backlog_bytes -eq 0)
    [void]$script:wave_jf_buffer.AddRange([byte[]](1..100))
    update_connected_status
    assert_wave_test 'status_backlog_shown' ($label_status.Text -match '积压 100 B' -and $script:rx_backlog_bytes -eq 100)
    for ($k = 0; $k -lt 5; $k++) {
        [void]$script:wave_jf_buffer.AddRange((New-Object byte[] 1000))
        update_connected_status
    }
    assert_wave_test 'status_backlog_warn' ($label_status.ForeColor -eq (get_status_color -tone 'warn'))
    $script:wave_jf_buffer.Clear()
    update_connected_status
    assert_wave_test 'status_backlog_gone' ($label_status.Text -match '积压 0 B' -and $script:rx_backlog_bytes -eq 0)
    $script:connected = $false
    $script:wave_enabled = $false

    # 空闲落显（无换行残行静默超阈值即显示，不再等 8KB 强制线；新鲜字节不触发）
    $script:rx_line_buffer = 'no_newline_tail'
    $script:rx_last_byte_at = (Get-Date).AddSeconds(-2)
    assert_wave_test 'idle_flush_fires' (flush_rx_line_if_idle)
    assert_wave_test 'idle_flush_cleared' ([string]::IsNullOrEmpty($script:rx_line_buffer))
    $script:rx_line_buffer = 'fresh_tail'
    $script:rx_last_byte_at = [datetime]::Now
    assert_wave_test 'idle_flush_holds_fresh' (-not (flush_rx_line_if_idle))
    $script:rx_line_buffer = ''

    # 版本比较（更新检查的纯逻辑核心）：三段数字、v 前缀、缺段补 0、非数字段按 0
    assert_wave_test 'ver_lt' ((compare_app_versions -left '1.0.9' -right '1.1.0') -eq -1)
    assert_wave_test 'ver_eq' ((compare_app_versions -left 'v1.1.0' -right '1.1.0') -eq 0)
    assert_wave_test 'ver_gt' ((compare_app_versions -left '1.2' -right 'v1.1.9') -eq 1)
    assert_wave_test 'ver_tuple' (((get_version_tuple -version_text 'v2.5') -join '.') -eq '2.5.0')

    # 文件发送状态机（假串口）：3 块写满 10000 字节自动收尾；中途停止不再续发
    if (-not ('V6FakeSerialPort' -as [type])) {
        Add-Type -TypeDefinition 'public class V6FakeSerialPort { public bool IsOpen { get { return true; } } public long Written; public void Write(byte[] buffer, int offset, int count) { Written += count; } }'
    }
    $fake_port = New-Object V6FakeSerialPort
    $prev_serial_port = $script:serial_port
    $script:serial_port = $fake_port
    $send_file_tmp = Join-Path $env:TEMP ('v6_send_{0}.bin' -f $PID)
    [System.IO.File]::WriteAllBytes($send_file_tmp, (New-Object byte[] 10000))
    start_serial_file_send -path $send_file_tmp
    assert_wave_test 'file_send_started' ($null -ne $script:file_send_state)
    for ($k = 0; $k -lt 5; $k++) { tick_serial_file_send }
    assert_wave_test 'file_send_done' ($null -eq $script:file_send_state -and $fake_port.Written -eq 10000)
    [System.IO.File]::WriteAllBytes($send_file_tmp, (New-Object byte[] 9000))
    start_serial_file_send -path $send_file_tmp
    [void](tick_serial_file_send)
    stop_serial_file_send -reason '用户停止'
    assert_wave_test 'file_send_stopped' ($null -eq $script:file_send_state -and $fake_port.Written -eq 14096)
    $script:serial_port = $prev_serial_port
    Remove-Item -LiteralPath $send_file_tmp -Force -ErrorAction SilentlyContinue

    # 日志查找：时间戳识别/解析、时间定位（跳到首条 ≥ 查询时刻的条目）、文本搜索与回绕
    assert_wave_test 'find_ts_detect' ((test_find_query_is_timestamp -query '20:57') -and (test_find_query_is_timestamp -query '2026-10-24 20:57:38.5') -and (-not (test_find_query_is_timestamp -query 'hello')))
    assert_wave_test 'find_ts_parse_bad' ($null -eq (parse_find_timestamp -query '25:99'))
    $textbox_rx = New-Object System.Windows.Forms.RichTextBox
    $textbox_rx.WordWrap = $false
    $textbox_rx.Size = New-Object System.Drawing.Size(400, 300)
    $script:find_box = New-Object DarkTextBox
    $script:find_label_count = New-Object System.Windows.Forms.Label
    [void]$textbox_rx.AppendText('[2026-10-24 10:00:00.000] RX' + "`r`n")
    [void]$textbox_rx.AppendText('value alpha' + "`r`n")
    [void]$textbox_rx.AppendText('[2026-10-24 10:05:00.000] RX' + "`r`n")
    [void]$textbox_rx.AppendText('value beta' + "`r`n")
    $script:find_box.Text = '10:03'
    $find_result = find_log_timestamp_jump -query '10:03' -backward:$false
    assert_wave_test 'find_ts_jump' ($find_result.Found -and $find_result.Ordinal -eq 2 -and $find_result.Total -eq 2)
    assert_wave_test 'find_ts_selected' ($textbox_rx.SelectedText -match '10:05:00')
    # 时间戳定位把选区留在了文档中部；文本搜索从选区后开始，这里归零模拟刚打开查找栏
    $textbox_rx.SelectionStart = 0
    $textbox_rx.SelectionLength = 0
    $script:find_box.Text = 'value'
    $f1 = find_log_text_jump -query 'value' -backward:$false
    assert_wave_test 'find_text_first' ($f1.Found -and $f1.Ordinal -eq 1 -and $textbox_rx.SelectedText -eq 'value')
    $f2 = find_log_text_jump -query 'value' -backward:$false
    assert_wave_test 'find_text_next' ($f2.Found -and $f2.Ordinal -eq 2)
    $f3 = find_log_text_jump -query 'value' -backward:$false
    assert_wave_test 'find_text_wrap' ($f3.Found -and $f3.Ordinal -eq 1)
    $fb = find_log_text_jump -query 'value' -backward:$true
    assert_wave_test 'find_text_backward' ($fb.Found -and $fb.Ordinal -eq 2)

    # 查找高亮：全部命中铺弱色、清除复原、输入即搜从头重启
    apply_find_highlights -query 'value'
    assert_wave_test 'find_highlight_applied' ($script:find_highlight_ranges.Count -eq 2)
    $textbox_rx.SelectionStart = $script:find_highlight_ranges[1][0]
    $textbox_rx.SelectionLength = $script:find_highlight_ranges[1][1]
    $hl_colors = get_find_highlight_backcolors
    assert_wave_test 'find_highlight_color' ($textbox_rx.SelectionBackColor -eq $hl_colors.All)
    $hl_first = $script:find_highlight_ranges[0]
    clear_find_highlights
    # 空选区读 SelectionBackColor 返回 Empty，须重新选中已清除的范围再读；
    # RTB 读回会把 0xFFFFFF 归一化成 KnownColor.White，比较用 ToArgb()
    $textbox_rx.SelectionStart = $hl_first[0]
    $textbox_rx.SelectionLength = $hl_first[1]
    $cleared_color = [System.Drawing.Color]::FromArgb($textbox_rx.BackColor.R, $textbox_rx.BackColor.G, $textbox_rx.BackColor.B)
    assert_wave_test 'find_highlight_cleared' ($script:find_highlight_ranges.Count -eq 0 -and $textbox_rx.SelectionBackColor.ToArgb() -eq $cleared_color.ToArgb())
    $textbox_rx.SelectionStart = $textbox_rx.TextLength
    $textbox_rx.SelectionLength = 0
    $script:find_box.Text = 'value'
    [void](invoke_log_find -restart:$true)
    assert_wave_test 'find_live_restart' ($textbox_rx.SelectionStart -lt 40 -and $script:find_label_count.Text -match '第 1/')
    [void](center_log_view_to_char -char_index 0)

    # Modbus RTU 插件：CRC 标准向量、建帧、响应解析、异常帧、线圈展开、float32 解码、假串口整链路
    # 顶层的 Register-WavePlugin 会返回注册对象：dot-source 时若不捕获，对象会
    # 直接抛进成功输出流；stdout 被重定向时（CI/测试环境）格式化该对象会杀死
    # WinPS 控制台输出写入器，之后所有 Write-Output 静默消失（含 SELFTEST_FAIL）
    $null = . (Join-Path $PSScriptRoot 'plugins\modbus_rtu\plugin.ps1')
    $mb_built = build_modbus_request -slave 1 -fc 3 -start 0 -count 2 -value_text '' -value_list @()
    assert_wave_test 'modbus_crc_vector' ($mb_built.Error -eq '' -and (bytes_to_hex_text -bytes $mb_built.Bytes) -eq '01 03 00 00 00 02 C4 0B')
    assert_wave_test 'modbus_crc_check' (test_modbus_crc -frame $mb_built.Bytes)
    $mb_w5 = build_modbus_request -slave 1 -fc 5 -start 0 -count 1 -value_text '1' -value_list @()
    assert_wave_test 'modbus_fc5' ((bytes_to_hex_text -bytes $mb_w5.Bytes) -eq '01 05 00 00 FF 00 8C 3A')
    $mb_w16 = build_modbus_request -slave 2 -fc 16 -start 100 -count 2 -value_text '100,0x2000' -value_list @('100', '0x2000')
    assert_wave_test 'modbus_fc16' ($mb_w16.Error -eq '' -and $mb_w16.Bytes.Length -eq 13 -and ((bytes_to_hex_text -bytes $mb_w16.Bytes[0..10]) -eq '02 10 00 64 00 02 04 00 64 20 00'))
    $resp_body = [byte[]](0x01, 0x03, 0x04, 0x41, 0x20, 0x00, 0x00)
    $resp_crc = get_modbus_crc16 -data $resp_body
    $resp_frame = New-Object byte[] 9
    [Array]::Copy($resp_body, $resp_frame, 7)
    $resp_frame[7] = $resp_crc[0]
    $resp_frame[8] = $resp_crc[1]
    if (-not ('V6FakeModbusPort' -as [type])) {
        Add-Type -TypeDefinition 'public class V6FakeModbusPort { public bool IsOpen { get { return true; } } public byte[] Staged = new byte[0]; public int Pos; public int BytesToRead { get { return Staged.Length - Pos; } } public int Read(byte[] buffer, int offset, int count) { int n = System.Math.Min(count, Staged.Length - Pos); System.Array.Copy(Staged, Pos, buffer, offset, n); Pos += n; return n; } public void DiscardInBuffer() { } public void Write(byte[] buffer, int offset, int count) { } }'
    }
    $modbus_fake = New-Object V6FakeModbusPort
    $prev_modbus_port = $script:serial_port
    $script:serial_port = $modbus_fake
    $modbus_fake.Staged = $resp_frame
    $modbus_fake.Pos = 0
    $mb_resp = send_modbus_wire_transaction -frame $mb_built.Bytes -timeout_ms 300
    assert_wave_test 'modbus_wire_roundtrip' ($mb_resp.Length -eq 9)
    $script:serial_port = $prev_modbus_port
    $mb_parsed = parse_modbus_response -resp $mb_resp -fc 3 -slave 1 -quantity 2
    assert_wave_test 'modbus_parse_regs' ($mb_parsed.Ok -and (decode_modbus_registers -reg_bytes $mb_parsed.RegBytes -mode '十进制') -eq '16672, 0')
    assert_wave_test 'modbus_float_abcd' ((decode_modbus_registers -reg_bytes $mb_parsed.RegBytes -mode 'float32(ABCD)') -eq '10')
    $exc_body = [byte[]](0x01, 0x83, 0x02)
    $exc_crc = get_modbus_crc16 -data $exc_body
    $exc_frame = [byte[]]@(0x01, 0x83, 0x02, $exc_crc[0], $exc_crc[1])
    $exc_parsed = parse_modbus_response -resp $exc_frame -fc 3 -slave 1 -quantity 1
    assert_wave_test 'modbus_exception' (-not $exc_parsed.Ok -and $exc_parsed.Exception -eq 2 -and $exc_parsed.Error -match '非法数据地址')
    $coil_body = [byte[]](0x01, 0x01, 0x01, 0x05)
    $coil_crc = get_modbus_crc16 -data $coil_body
    $coil_frame = [byte[]]@(0x01, 0x01, 0x01, 0x05, $coil_crc[0], $coil_crc[1])
    $coil_parsed = parse_modbus_response -resp $coil_frame -fc 1 -slave 1 -quantity 3
    assert_wave_test 'modbus_coils' ($coil_parsed.Ok -and (($coil_parsed.Coils -join ',') -eq '1,0,1'))

    # 实时监视纯逻辑：键 upsert / 分组合并（近邻合并、跨上限拆组）/ 地址切片回填
    $watch_list = New-Object 'System.Collections.Generic.List[object]'
    [void](upsert_modbus_watch_item -list $watch_list -item ([pscustomobject]@{ Key = '1:3:0'; Name = 'a'; Slave = 1; Fc = 3; Start = 0; Count = 2; Decode = '十进制'; LastValue = ''; LastStatus = '' }))
    [void](upsert_modbus_watch_item -list $watch_list -item ([pscustomobject]@{ Key = '1:3:0'; Name = 'a2'; Slave = 1; Fc = 3; Start = 0; Count = 4; Decode = 'HEX'; LastValue = ''; LastStatus = '' }))
    assert_wave_test 'modbus_watch_upsert' ($watch_list.Count -eq 1 -and $watch_list[0].Name -eq 'a2' -and $watch_list[0].Count -eq 4)
    $w_groups = merge_modbus_watch_groups -items @(
        [pscustomobject]@{ Key = '1:3:0'; Slave = 1; Fc = 3; Start = 0; Count = 2 },
        [pscustomobject]@{ Key = '1:3:4'; Slave = 1; Fc = 3; Start = 4; Count = 2 },
        [pscustomobject]@{ Key = '1:3:100'; Slave = 1; Fc = 3; Start = 100; Count = 2 },
        [pscustomobject]@{ Key = '2:3:0'; Slave = 2; Fc = 3; Start = 0; Count = 1 }
    )
    assert_wave_test 'modbus_watch_merge' ($w_groups.Count -eq 3 -and $w_groups[0].Count -eq 6 -and $w_groups[0].Items.Count -eq 2)
    $w_slice = slice_modbus_watch_value -reg_bytes ([byte[]]@(0x00, 0x01, 0x00, 0x02, 0x00, 0x03, 0x00, 0x04)) -fc 3 -group_start 0 -item ([pscustomobject]@{ Start = 2; Count = 2; Decode = '十进制' })
    assert_wave_test 'modbus_watch_slice' ($w_slice.Text -eq '3, 4' -and $w_slice.Raw -eq '00 03 00 04')
    $w_flash = get_modbus_watch_flash_color -old_text '10' -new_text '11'
    $w_flash2 = get_modbus_watch_flash_color -old_text '10' -new_text '9'
    assert_wave_test 'modbus_watch_flash' ($w_flash.ToArgb() -ne [System.Drawing.Color]::Empty.ToArgb() -and $w_flash2.ToArgb() -ne $w_flash.ToArgb() -and (get_modbus_watch_flash_color -old_text '10' -new_text '10').ToArgb() -eq [System.Drawing.Color]::Empty.ToArgb())

    if ($failures.Count -gt 0) {
        Write-Output ('SELFTEST_FAIL: ' + ($failures -join '; '))
        exit 1
    }
    Write-Output 'SELFTEST_OK'
    exit 0
}

if ($Headless) {
    try {
        $headless_exit = start_headless_session -PortName $Port -BaudRate $Baud -ProtocolName $Protocol -DurationSeconds $Seconds -OutFile $Out -ErrorRegex $HitPattern -TriggerFile $TriggerFile -ExpectFile $ExpectFile -RecordPath $RecordFile -ReplayPath $ReplayFile -EnableDtr $Dtr -EnableRts $Rts -FlashCommand $FlashCommand -FlashTimeoutMs $FlashTimeoutMs -ResetMode $ResetMode -ResetCommand $ResetCommand
        exit $headless_exit
    }
    catch {
        Write-Host ('HEADLESS_ERROR: ' + $_.Exception.Message)
        exit 1
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'RK3562 串口收发工具 V6'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(1360, 820)
$form.MinimumSize = New-Object System.Drawing.Size(1270, 760)
$form.Font = new_font -name 'Microsoft YaHei UI' -size 9.5
$form.BackColor = [System.Drawing.Color]::FromArgb(245, 246, 248)

$font_box = new_font -size 10
$font_header = new_font -size 9 -style Bold

$panel_top = New-Object System.Windows.Forms.Panel
$panel_top.Dock = 'Top'
# 高度只装两行功能行 + 可收展的板卡操作行：状态行已移到底部状态条。
# 收起 82（两行：y=10 / y=48），展开 124（第三行 y=88）
$panel_top.Height = 124
# 底边 Padding 归零：下面的 $sep_top 才能贴到面板最下沿画线。
# 其余三边保持 10（面板内控件都是绝对定位，本来不吃 Padding）
$panel_top.Padding = New-Object System.Windows.Forms.Padding(10, 10, 10, 0)
$panel_top.BackColor = [System.Drawing.Color]::White

# 1px 分隔线：只用色差分不出区域边界，必须有线。放面板内而不是 form 上，
# 这样不依赖 Controls.Add 的 z-order 顺序（WinForms dock 按集合逆序布局）。
$sep_top = New-Object System.Windows.Forms.Panel
$sep_top.Dock = 'Bottom'
$sep_top.Height = 1
$sep_top.BackColor = [System.Drawing.Color]::FromArgb(150, 150, 150)
$panel_top.Controls.Add($sep_top)

$label_port = New-Object System.Windows.Forms.Label
$label_port.Text = '串口'
$label_port.Location = New-Object System.Drawing.Point(10, 15)
$label_port.AutoSize = $true

$combobox_port = New-Object DarkComboBox
$combobox_port.Location = New-Object System.Drawing.Point(60, 10)
$combobox_port.Size = New-Object System.Drawing.Size(215, 28)
$combobox_port.DropDownStyle = 'DropDownList'

$button_refresh = New-Object System.Windows.Forms.Button
$button_refresh.Text = '刷新串口'
$button_refresh.Location = New-Object System.Drawing.Point(285, 9)
$button_refresh.Size = New-Object System.Drawing.Size(90, 30)

$label_baud = New-Object System.Windows.Forms.Label
$label_baud.Text = '波特率'
$label_baud.Location = New-Object System.Drawing.Point(390, 15)
$label_baud.AutoSize = $true

$combobox_baud = New-Object DarkComboBox
$combobox_baud.Location = New-Object System.Drawing.Point(450, 10)
$combobox_baud.Size = New-Object System.Drawing.Size(110, 28)
$combobox_baud.DropDownStyle = 'DropDown'
[void]$combobox_baud.Items.AddRange(@('1500000', '115200', '921600', '460800', '230400', '57600', '38400', '19200', '9600'))
$combobox_baud.Text = '1500000'

# ---- 行 1（连接行）：选口/刷新/波特率 + 连接/断开 + 显示偏好（主题/收发格式）+ 波形窗口 ----
$label_data_bits = New-Object System.Windows.Forms.Label
$label_data_bits.Text = '数据位'
$label_data_bits.Location = New-Object System.Drawing.Point(290, 55)
$label_data_bits.AutoSize = $true

$combobox_data_bits = New-Object DarkComboBox
$combobox_data_bits.Location = New-Object System.Drawing.Point(345, 50)
$combobox_data_bits.Size = New-Object System.Drawing.Size(70, 28)
$combobox_data_bits.DropDownStyle = 'DropDownList'
[void]$combobox_data_bits.Items.AddRange(@('8', '7', '6', '5'))
$combobox_data_bits.SelectedItem = '8'

$label_parity = New-Object System.Windows.Forms.Label
$label_parity.Text = '校验'
$label_parity.Location = New-Object System.Drawing.Point(430, 55)
$label_parity.AutoSize = $true

$combobox_parity = New-Object DarkComboBox
$combobox_parity.Location = New-Object System.Drawing.Point(475, 50)
$combobox_parity.Size = New-Object System.Drawing.Size(90, 28)
$combobox_parity.DropDownStyle = 'DropDownList'
[void]$combobox_parity.Items.AddRange(@('None', 'Odd', 'Even', 'Mark', 'Space'))
$combobox_parity.SelectedItem = 'None'

$label_stop_bits = New-Object System.Windows.Forms.Label
$label_stop_bits.Text = '停止位'
$label_stop_bits.Location = New-Object System.Drawing.Point(580, 55)
$label_stop_bits.AutoSize = $true

$combobox_stop_bits = New-Object DarkComboBox
$combobox_stop_bits.Location = New-Object System.Drawing.Point(635, 50)
$combobox_stop_bits.Size = New-Object System.Drawing.Size(80, 28)
$combobox_stop_bits.DropDownStyle = 'DropDownList'
[void]$combobox_stop_bits.Items.AddRange(@('One', 'OnePointFive', 'Two'))
$combobox_stop_bits.SelectedItem = 'One'

$label_flow_control = New-Object System.Windows.Forms.Label
$label_flow_control.Text = '流控'
$label_flow_control.Location = New-Object System.Drawing.Point(730, 55)
$label_flow_control.AutoSize = $true

$combobox_flow_control = New-Object DarkComboBox
$combobox_flow_control.Location = New-Object System.Drawing.Point(770, 50)
$combobox_flow_control.Size = New-Object System.Drawing.Size(110, 28)
$combobox_flow_control.DropDownStyle = 'DropDownList'
[void]$combobox_flow_control.Items.AddRange(@('None', 'XOnXOff', 'RequestToSend', 'RequestToSendXOnXOff'))
$combobox_flow_control.SelectedItem = 'None'

# 波形窗口按钮留在行 1 右端（见下方 $button_wave 坐标）

$button_wave = New-Object System.Windows.Forms.Button
$button_wave.Text = '波形窗口'
$button_wave.Location = New-Object System.Drawing.Point(1225, 9)
$button_wave.Size = New-Object System.Drawing.Size(100, 30)

$checkbox_dtr = New-Object DarkCheckBox
$checkbox_dtr.Text = 'DTR'
$checkbox_dtr.Location = New-Object System.Drawing.Point(60, 50)
$checkbox_dtr.AutoSize = $true

$checkbox_rts = New-Object DarkCheckBox
$checkbox_rts.Text = 'RTS'
$checkbox_rts.Location = New-Object System.Drawing.Point(120, 50)
$checkbox_rts.AutoSize = $true

$checkbox_auto_save = New-Object DarkCheckBox
$checkbox_auto_save.Text = '自动保存日志'
$checkbox_auto_save.Location = New-Object System.Drawing.Point(180, 50)
$checkbox_auto_save.AutoSize = $true
$checkbox_auto_save.Checked = $true

$label_theme = New-Object System.Windows.Forms.Label
$label_theme.Text = '主题'
$label_theme.Location = New-Object System.Drawing.Point(765, 15)
$label_theme.AutoSize = $true

$combobox_theme = New-Object DarkComboBox
$combobox_theme.Location = New-Object System.Drawing.Point(800, 10)
$combobox_theme.Size = New-Object System.Drawing.Size(90, 28)
$combobox_theme.DropDownStyle = 'DropDownList'
[void]$combobox_theme.Items.AddRange(@('Dark', 'Light'))
$combobox_theme.SelectedItem = 'Dark'

$label_rx_format = New-Object System.Windows.Forms.Label
$label_rx_format.Text = '接收格式'
$label_rx_format.Location = New-Object System.Drawing.Point(905, 15)
$label_rx_format.AutoSize = $true

$combobox_rx_format = New-Object DarkComboBox
$combobox_rx_format.Location = New-Object System.Drawing.Point(970, 10)
$combobox_rx_format.Size = New-Object System.Drawing.Size(80, 28)
$combobox_rx_format.DropDownStyle = 'DropDownList'
[void]$combobox_rx_format.Items.AddRange(@('ASCII', 'HEX'))
$combobox_rx_format.SelectedItem = 'ASCII'

$label_tx_format = New-Object System.Windows.Forms.Label
$label_tx_format.Text = '发送格式'
$label_tx_format.Location = New-Object System.Drawing.Point(1065, 15)
$label_tx_format.AutoSize = $true

$combobox_tx_format = New-Object DarkComboBox
$combobox_tx_format.Location = New-Object System.Drawing.Point(1130, 10)
$combobox_tx_format.Size = New-Object System.Drawing.Size(80, 28)
$combobox_tx_format.DropDownStyle = 'DropDownList'
[void]$combobox_tx_format.Items.AddRange(@('ASCII', 'HEX'))
$combobox_tx_format.SelectedItem = 'ASCII'

$button_connect = New-Object System.Windows.Forms.Button
$button_connect.Text = '连接'
$button_connect.Location = New-Object System.Drawing.Point(575, 9)
$button_connect.Size = New-Object System.Drawing.Size(80, 30)

$button_disconnect = New-Object System.Windows.Forms.Button
$button_disconnect.Text = '断开'
$button_disconnect.Location = New-Object System.Drawing.Point(665, 9)
$button_disconnect.Size = New-Object System.Drawing.Size(80, 30)

# 连接/断开的配色与互斥状态统一由 apply_theme → sync_connect_buttons 处理，
# 这里不要设 Enabled（禁用态在深底上不可读）；也不要再调 style_wave_button，
# 它读的是波形窗的 $script:theme，主窗体要用 $script:ui。

$button_clear = New-Object System.Windows.Forms.Button
$button_clear.Text = '清空记录'
$button_clear.Location = New-Object System.Drawing.Point(900, 46)
$button_clear.Size = New-Object System.Drawing.Size(80, 32)

$button_save = New-Object System.Windows.Forms.Button
$button_save.Text = '导出日志'
$button_save.Location = New-Object System.Drawing.Point(990, 46)
$button_save.Size = New-Object System.Drawing.Size(90, 32)

$button_help = New-Object System.Windows.Forms.Button
$button_help.Text = '帮助'
$button_help.Location = New-Object System.Drawing.Point(1225, 46)
$button_help.Size = New-Object System.Drawing.Size(100, 32)

# ---- 状态行被移出顶部面板，改为底部状态条（见 $panel_status） ----
$label_status_title = New-Object System.Windows.Forms.Label
$label_status_title.Text = '状态'
$label_status_title.Location = New-Object System.Drawing.Point(10, 7)
$label_status_title.AutoSize = $true

$label_status = New-Object System.Windows.Forms.Label
$label_status.Text = '未连接'
$label_status.Location = New-Object System.Drawing.Point(46, 5)
# 状态文本长度不可控（"已连接: COM8 @ 1500000" 这类），AutoSize 会一直往右长，
# 撞上右边的日志路径标签。改成定宽 + 省略号。
$label_status.AutoSize = $false
$label_status.AutoEllipsis = $true
$label_status.Size = New-Object System.Drawing.Size(200, 18)
# 创建期就取 token，不写死 DarkRed：apply_theme 启动时会再刷一次，
# 但写死固定深色色等于给「apply_theme 之前」的任何调用留一颗雷
$label_status.ForeColor = $script:ui.ErrText

$label_log_path = New-Object System.Windows.Forms.Label
$label_log_path.Text = '自动日志将在首次连接后生成到 logs 目录'
$label_log_path.Location = New-Object System.Drawing.Point(258, 5)
# 同上：日志路径带完整目录 + 时间戳，是全场最长的动态文本，必须定宽。
# 右边界留给 checkbox_autolink（x=800），留 8px 间隙。
$label_log_path.AutoSize = $false
$label_log_path.AutoEllipsis = $true
$label_log_path.Size = New-Object System.Drawing.Size(534, 18)

$checkbox_autolink = New-Object DarkCheckBox
$checkbox_autolink.Text = '新设备自动连接'
$checkbox_autolink.Location = New-Object System.Drawing.Point(800, 4)
$checkbox_autolink.AutoSize = $true
$checkbox_autolink.Checked = $true

$panel_status = New-Object System.Windows.Forms.Panel
$panel_status.Dock = 'Bottom'
$panel_status.Height = 28
$panel_status.Padding = New-Object System.Windows.Forms.Padding(10, 0, 10, 0)
$panel_status.BackColor = $script:ui.StatusBg
$panel_status.ForeColor = $script:ui.StatusFg
$panel_status.Controls.AddRange(@(
    $label_status_title,
    $label_status,
    $label_log_path,
    $checkbox_autolink
))

$button_actions_toggle = New-Object System.Windows.Forms.Button
$button_actions_toggle.Text = '−'
$button_actions_toggle.Location = New-Object System.Drawing.Point(10, 86)
$button_actions_toggle.Size = New-Object System.Drawing.Size(28, 30)

$label_actions = New-Object System.Windows.Forms.Label
$label_actions.Text = '板卡操作'
$label_actions.Location = New-Object System.Drawing.Point(46, 92)
$label_actions.AutoSize = $true

$combobox_reset_mode = New-Object DarkComboBox
$combobox_reset_mode.Location = New-Object System.Drawing.Point(80, 87)
$combobox_reset_mode.Size = New-Object System.Drawing.Size(120, 28)
$combobox_reset_mode.DropDownStyle = 'DropDownList'
[void]$combobox_reset_mode.Items.AddRange(@('RTS 脉冲', 'DTR 脉冲'))
$combobox_reset_mode.SelectedItem = 'RTS 脉冲'

$button_reset = New-Object System.Windows.Forms.Button
$button_reset.Text = '复位板卡'
$button_reset.Location = New-Object System.Drawing.Point(210, 86)
$button_reset.Size = New-Object System.Drawing.Size(90, 30)

$textbox_flash_cmd = New-Object System.Windows.Forms.TextBox
$textbox_flash_cmd.Location = New-Object System.Drawing.Point(310, 87)
$textbox_flash_cmd.Size = New-Object System.Drawing.Size(640, 28)
$textbox_flash_cmd.Font = new_font -size 9

$button_flash = New-Object System.Windows.Forms.Button
$button_flash.Text = '执行并抓取'
$button_flash.Location = New-Object System.Drawing.Point(960, 86)
$button_flash.Size = New-Object System.Drawing.Size(110, 30)

$button_hit_clear = New-Object System.Windows.Forms.Button
$button_hit_clear.Text = '清除高亮'
$button_hit_clear.Location = New-Object System.Drawing.Point(1080, 86)
$button_hit_clear.Size = New-Object System.Drawing.Size(90, 30)

$panel_top.Controls.AddRange(@(
    $label_port,
    $combobox_port,
    $button_refresh,
    $label_baud,
    $combobox_baud,
    $label_data_bits,
    $combobox_data_bits,
    $label_parity,
    $combobox_parity,
    $label_stop_bits,
    $combobox_stop_bits,
    $label_flow_control,
    $combobox_flow_control,
    $button_wave,
    $checkbox_dtr,
    $checkbox_rts,
    $checkbox_auto_save,
    $label_theme,
    $combobox_theme,
    $label_rx_format,
    $combobox_rx_format,
    $label_tx_format,
    $combobox_tx_format,
    $button_connect,
    $button_disconnect,
    $button_clear,
    $button_save,
    $button_help,
    $button_actions_toggle,
    $label_actions,
    $combobox_reset_mode,
    $button_reset,
    $textbox_flash_cmd,
    $button_flash,
    $button_hit_clear
))

$split_logs = New-Object System.Windows.Forms.SplitContainer
$split_logs.Dock = 'Fill'
$split_logs.Orientation = 'Vertical'
$split_logs.SplitterDistance = 625
# 3px：够抓（拖动热区被 WinForms 放大到 8px 左右，手感不受影响），又不至于割裂画面
$split_logs.SplitterWidth = 3
$split_logs.BackColor = [System.Drawing.Color]::FromArgb(230, 232, 236)
$split_logs.Panel1.BackColor = [System.Drawing.Color]::FromArgb(15, 17, 22)
$split_logs.Panel2.BackColor = [System.Drawing.Color]::FromArgb(23, 31, 46)

$label_rx = New-Object System.Windows.Forms.Label
$label_rx.Text = '串口返回 RX'
$label_rx.Dock = 'Top'
$label_rx.Height = 30
$label_rx.TextAlign = 'MiddleLeft'
$label_rx.Padding = New-Object System.Windows.Forms.Padding(10, 0, 0, 0)
$label_rx.ForeColor = [System.Drawing.Color]::White
# 细字标题：不再用"色块 + 粗体"抢层级，层级交给底部状态条和分隔线承担
$label_rx.Font = new_font -size 9

$textbox_rx = New-Object DarkRichTextBox
$textbox_rx.Dock = 'Fill'
$textbox_rx.Multiline = $true
$textbox_rx.ReadOnly = $true
# 干掉 Fixed3D 亮边：日志区边界由分隔条与面板承担，3D 边框的白边在暗色主题下
# 就是分隔条旁边那道"过渡太深"的亮线
$textbox_rx.BorderStyle = 'None'
# 只留纵向滚动条：Both 会让横条常驻，暗色主题下是两道亮色缝（截图实测最扎眼处）
$textbox_rx.ScrollBars = 'Vertical'
$textbox_rx.WordWrap = $false
$textbox_rx.Font = $font_box
$textbox_rx.BackColor = $script:ui.LogBgRx
$textbox_rx.ForeColor = $script:ui.LogTextRx

$split_logs.Panel1.Controls.Add($textbox_rx)
$split_logs.Panel1.Controls.Add($label_rx)

$label_tx = New-Object System.Windows.Forms.Label
$label_tx.Text = '用户发送 TX'
$label_tx.Dock = 'Top'
$label_tx.Height = 24
$label_tx.TextAlign = 'MiddleLeft'
$label_tx.Padding = New-Object System.Windows.Forms.Padding(10, 0, 0, 0)
$label_tx.ForeColor = [System.Drawing.Color]::White
$label_tx.Font = new_font -size 9

$textbox_tx = New-Object DarkTextBox
$textbox_tx.Dock = 'Fill'
$textbox_tx.Multiline = $true
$textbox_tx.ReadOnly = $true
$textbox_tx.BorderStyle = 'None'
$textbox_tx.ScrollBars = 'Vertical'
$textbox_tx.WordWrap = $false
$textbox_tx.Font = $font_box
$textbox_tx.BackColor = [System.Drawing.Color]::FromArgb(23, 31, 46)
$textbox_tx.ForeColor = [System.Drawing.Color]::White

$split_logs.Panel2.Controls.Add($textbox_tx)
$split_logs.Panel2.Controls.Add($label_tx)

# 日志行悬停格式互转：HEX 行解码成 文本/十进制/float32，文本行给 UTF-8 HEX。
# tooltip 组件挂脚本变量防 GC；逻辑在 core\serial_io.ps1（handle_log_hover_move/leave）
$script:log_hover_tip = New-Object System.Windows.Forms.ToolTip
$script:log_hover_tip.InitialDelay = 400
$script:log_hover_tip.ReshowDelay = 80
$script:log_hover_tip.AutoPopDelay = 20000
$script:log_hover_state = @{ Line = -1; Text = '' }
$textbox_rx.Add_MouseMove({
    param($sender, $event_args)
    handle_log_hover_move -box $sender -location $event_args.Location
})
$textbox_rx.Add_MouseLeave({ handle_log_hover_leave })
$textbox_tx.Add_MouseMove({
    param($sender, $event_args)
    handle_log_hover_move -box $sender -location $event_args.Location
})
$textbox_tx.Add_MouseLeave({ handle_log_hover_leave })

# ---- 查找栏（Ctrl+F 呼出）：文本搜索 + 时间戳快速定位 ----
# 挂在 RX 区 Panel1 里 Dock=Top（最后添加 → 停靠在最上沿），隐藏时不占布局
$script:find_panel = New-Object System.Windows.Forms.Panel
$script:find_panel.Dock = 'Top'
$script:find_panel.Height = 36
$script:find_panel.Visible = $false
$script:find_panel.BackColor = $script:ui.PanelBg

$label_find = New-Object System.Windows.Forms.Label
$label_find.Text = '查找:'
$label_find.Location = New-Object System.Drawing.Point(10, 10)
$label_find.AutoSize = $true
$label_find.ForeColor = $script:ui.TextDim

$script:find_box = New-Object DarkTextBox
$script:find_box.Location = New-Object System.Drawing.Point(46, 6)
$script:find_box.Size = New-Object System.Drawing.Size(250, 24)
$script:find_box.BorderStyle = 'None'
$script:find_box.Font = $font_box
# 输入 20:57:38 这类时刻 → 直接跳到第一条 ≥ 该时间的日志头行；其余按文本搜
$script:find_box.Add_KeyDown({
    param($sender, $event_args)
    if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $event_args.SuppressKeyPress = $true
        invoke_log_find -backward:$event_args.Shift
    }
    elseif ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Escape) {
        $event_args.SuppressKeyPress = $true
        toggle_find_panel -show:$false
    }
})

$button_find_prev = New-Object System.Windows.Forms.Button
$button_find_prev.Text = '上一个'
$button_find_prev.Location = New-Object System.Drawing.Point(304, 5)
$button_find_prev.Size = New-Object System.Drawing.Size(62, 25)
$button_find_prev.Add_Click({ invoke_log_find -backward:$true })

$button_find_next = New-Object System.Windows.Forms.Button
$button_find_next.Text = '下一个'
$button_find_next.Location = New-Object System.Drawing.Point(370, 5)
$button_find_next.Size = New-Object System.Drawing.Size(62, 25)
$button_find_next.Add_Click({ invoke_log_find })

$script:find_label_count = New-Object System.Windows.Forms.Label
$script:find_label_count.Text = ''
$script:find_label_count.Location = New-Object System.Drawing.Point(442, 10)
$script:find_label_count.AutoSize = $true
$script:find_label_count.ForeColor = $script:ui.TextDim

$button_find_close = New-Object System.Windows.Forms.Button
$button_find_close.Text = [string][char]0x2715
$button_find_close.Location = New-Object System.Drawing.Point(1258, 5)
$button_find_close.Size = New-Object System.Drawing.Size(28, 25)
$button_find_close.Add_Click({ toggle_find_panel -show:$false })

# 输入即搜：150ms 防抖后从头重新匹配 + 高亮（Enter/按钮才是继续从当前位置跳）
$script:find_live_timer = New-Object System.Windows.Forms.Timer
$script:find_live_timer.Interval = 150
$script:find_live_timer.Add_Tick({
    # WinForms Timer 是周期性的：tick 必须先自停，否则查找栏开着就每 150ms
    # 从文档头重搜一次，「下一个 / 上一个」刚跳过去就被拽回首处
    $script:find_live_timer.Stop()
    invoke_log_find -restart:$true
})
$script:find_box.Add_TextChanged({
    $script:find_live_timer.Stop()
    $script:find_live_timer.Start()
})

$script:find_panel.Controls.AddRange(@(
    $label_find,
    $script:find_box,
    $button_find_prev,
    $button_find_next,
    $script:find_label_count,
    $button_find_close
))
$split_logs.Panel1.Controls.Add($script:find_panel)

foreach ($button in @($button_find_prev, $button_find_next, $button_find_close)) {
    style_main_button -button $button
}
# ✕ 按钮贴右侧：主窗体可被拉宽，锚定 Right 跟随
$button_find_close.Anchor = 'Top,Right'

$form.KeyPreview = $true
$form.Add_KeyDown({
    param($sender, $event_args)
    if ($event_args.Control -and $event_args.KeyCode -eq [System.Windows.Forms.Keys]::F) {
        $event_args.SuppressKeyPress = $true
        toggle_find_panel
        return
    }
    if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::F3) {
        $event_args.SuppressKeyPress = $true
        invoke_log_find -backward:$event_args.Shift
        return
    }
    if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Escape -and $script:find_panel.Visible) {
        toggle_find_panel -show:$false
    }
})

$panel_bottom = New-Object System.Windows.Forms.Panel
$panel_bottom.Dock = 'Bottom'
# 高度按 8px 网格算：标题 8 → 输入区 32..104 → 提示 118..138 → 下边距 8
$panel_bottom.Height = 146
# 顶边 Padding 归零，配合下面的 $sep_bottom 把线贴在面板最上沿
$panel_bottom.Padding = New-Object System.Windows.Forms.Padding(10, 0, 10, 10)
$panel_bottom.BackColor = [System.Drawing.Color]::White

$sep_bottom = New-Object System.Windows.Forms.Panel
$sep_bottom.Dock = 'Top'
$sep_bottom.Height = 1
$sep_bottom.BackColor = [System.Drawing.Color]::FromArgb(150, 150, 150)
$panel_bottom.Controls.Add($sep_bottom)

$label_send = New-Object System.Windows.Forms.Label
$label_send.Text = '发送区'
$label_send.Location = New-Object System.Drawing.Point(10, 10)
$label_send.AutoSize = $true

$textbox_send = New-Object DarkTextBox
$textbox_send.Location = New-Object System.Drawing.Point(10, 32)
$textbox_send.Size = New-Object System.Drawing.Size(980, 72)
$textbox_send.Multiline = $true
$textbox_send.ScrollBars = 'Vertical'
$textbox_send.Font = $font_box

$button_send = New-Object System.Windows.Forms.Button
$button_send.Text = '发送'
$button_send.Location = New-Object System.Drawing.Point(1005, 32)
# 收窄到 68 给同一行的「文件发送」让位；换行下拉随之右移缩窄（窗体最小宽 1270 内不溢出）
$button_send.Size = New-Object System.Drawing.Size(68, 30)

$button_send_file = New-Object System.Windows.Forms.Button
$button_send_file.Text = '文件发送'
$button_send_file.Location = New-Object System.Drawing.Point(1077, 32)
$button_send_file.Size = New-Object System.Drawing.Size(82, 30)

$label_newline = New-Object System.Windows.Forms.Label
$label_newline.Text = '换行'
$label_newline.Location = New-Object System.Drawing.Point(1180, 10)
$label_newline.AutoSize = $true

$combobox_newline = New-Object DarkComboBox
$combobox_newline.Location = New-Object System.Drawing.Point(1180, 32)
$combobox_newline.Size = New-Object System.Drawing.Size(66, 28)
$combobox_newline.DropDownStyle = 'DropDownList'
[void]$combobox_newline.Items.AddRange(@('None', 'CR', 'LF', 'CRLF'))
$combobox_newline.SelectedItem = 'CRLF'

$checkbox_append_newline = New-Object DarkCheckBox
$checkbox_append_newline.Text = '发送时追加换行'
$checkbox_append_newline.Location = New-Object System.Drawing.Point(1005, 70)
$checkbox_append_newline.AutoSize = $true
$checkbox_append_newline.Checked = $true

$checkbox_clear_after_send = New-Object DarkCheckBox
$checkbox_clear_after_send.Text = '发送后清空输入'
$checkbox_clear_after_send.Location = New-Object System.Drawing.Point(1005, 92)
$checkbox_clear_after_send.AutoSize = $true
$checkbox_clear_after_send.Checked = $true

$label_hint = New-Object System.Windows.Forms.Label
$label_hint.Text = '左侧记录串口返回，右侧记录用户发送；点「帮助」看全部功能：Ctrl+F 查找与时间戳定位 / 悬停日志行自动 HEX⇄文本互转 / ⬆⬇ 回翻发送历史 / 波形窗口含 Modbus 插件。'
$label_hint.Location = New-Object System.Drawing.Point(10, 116)
# 这条提示也是长文本且 AutoSize，窗体一窄就会溢出面板被裁掉一半。
# 定宽 + 省略号 + 左右锚定，随窗体一起伸缩。
$label_hint.AutoSize = $false
$label_hint.AutoEllipsis = $true
$label_hint.Anchor = 'Left,Right'
$label_hint.Size = New-Object System.Drawing.Size(1340, 20)
$label_hint.ForeColor = [System.Drawing.Color]::DimGray

$panel_bottom.Controls.AddRange(@(
    $label_send,
    $textbox_send,
    $button_send,
    $button_send_file,
    $label_newline,
    $combobox_newline,
    $checkbox_append_newline,
    $checkbox_clear_after_send,
    $label_hint
))

$form.Controls.Add($split_logs)
$form.Controls.Add($panel_bottom)
# 状态条要在发送面板【之后】添加：WinForms 停靠按集合逆序裁决优先级，
# 后加的先贴边，这样状态条才落在最底部、发送面板退居其上。
$form.Controls.Add($panel_status)
$form.Controls.Add($panel_top)

# 启动时加载插件：必须【脚本顶层】dot-source——
# 顶层 foreach/if 语句块不创建作用域，插件函数才真正进入脚本作用域；
# 任何函数壳（如 import_wave_plugins()）都会把插件函数困在局部作用域，
# 事件触发时按脚本作用域链查找将全部失联（已两次踩坑，勿改回函数形式）
$plugins_root = Join-Path -Path $PSScriptRoot -ChildPath 'plugins'
if (Test-Path -LiteralPath $plugins_root) {
    foreach ($plugin_dir in (Get-ChildItem -LiteralPath $plugins_root -Directory -ErrorAction SilentlyContinue)) {
        $plugin_file = Join-Path $plugin_dir.FullName 'plugin.ps1'
        if (-not (Test-Path -LiteralPath $plugin_file)) {
            continue
        }
        try {
            # 同 SelfTest：插件顶层 Register 返回对象，捕获掉，防止重定向 stdout
            # 时格式化输出对象杀死 WinPS 输出写入器（后续全部 Write-Output 失声）
            $null = . $plugin_file
        }
        catch {
            write_runtime_log -message ('插件加载失败 {0} {1}:{2}行: {3}' -f $plugin_dir.Name, (Split-Path -Leaf $_.InvocationInfo.ScriptName), $_.InvocationInfo.ScriptLineNumber, $_.Exception.Message)
        }
    }
}

$combobox_theme.Add_SelectedIndexChanged({
    apply_theme -theme_name ([string]$combobox_theme.SelectedItem)
    save_gui_setting -key 'theme' -value ([string]$combobox_theme.SelectedItem)
})

$combobox_rx_format.Add_SelectedIndexChanged({
    if ($combobox_rx_format.SelectedItem -eq 'HEX') {
        flush_rx_line_buffer
    }
})

$button_refresh.Add_Click({
    try {
        refresh_ports
        update_status -text '串口列表已刷新' -tone 'info'
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '刷新失败', 'OK', 'Error') | Out-Null
    }
})

$checkbox_autolink.Add_CheckedChanged({
    save_gui_setting -key 'auto_link' -value $(if ($checkbox_autolink.Checked) { '1' } else { '0' })
})

$script:actions_expanded = $false

# 默认收起板卡操作行，仅保留状态行右端的 + 按钮与提示
set_actions_expanded -expanded $false

$button_actions_toggle.Add_Click({
    set_actions_expanded -expanded (-not $script:actions_expanded)
})

$button_wave.Add_Click({
    open_wave_window
})

$button_reset.Add_Click({
    try {
        if ($script:connected) {
            # 已连接：直接在当前端口上打脉冲，不打断连接
            $port = $script:serial_port
            $mode_text = [string]$combobox_reset_mode.SelectedItem
            if ($mode_text -eq 'DTR 脉冲') {
                $port.DtrEnable = $true
                Start-Sleep -Milliseconds 150
                $port.DtrEnable = $false
            }
            else {
                $port.RtsEnable = $true
                Start-Sleep -Milliseconds 150
                $port.RtsEnable = $false
            }
        }
        else {
            $mode_text = [string]$combobox_reset_mode.SelectedItem
            $pulse_mode = 'rts'
            if ($mode_text -eq 'DTR 脉冲') {
                $pulse_mode = 'dtr'
            }
            invoke_reset_pulse -mode $pulse_mode
        }
        add_message_entry -direction 'SYS' -text ('已发送复位脉冲（{0}）' -f $combobox_reset_mode.SelectedItem)
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '复位失败', 'OK', 'Error') | Out-Null
    }
})

$button_flash.Add_Click({
    $command = $textbox_flash_cmd.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($command)) {
        [System.Windows.Forms.MessageBox]::Show('请输入烧录/命令行。', '提示', 'OK', 'Information') | Out-Null
        return
    }
    if ($script:gui_busy) {
        return
    }

    $script:gui_busy = $true
    set_main_button_enabled -button $button_flash -enabled $false
    $button_flash.Text = '执行中...'
    try {
        $was_connected = $script:connected
        if ($script:connected) {
            close_serial_port
        }
        update_status -text '正在执行命令...' -tone 'info'
        $result = invoke_stream_command -command $command -timeout_ms 600000 -PumpUi
        if ($was_connected) {
            try {
                connect_serial_port
            }
            catch {
                write_runtime_log -message ('烧录后重连失败: {0}' -f $_.Exception.Message)
            }
        }
        $tail = @($result.lines | Select-Object -Last 6) -join ' | '
        if ($result.timed_out) {
            update_status -text '命令执行超时' -tone 'err'
            add_message_entry -direction 'SYS' -text ('命令超时: {0}' -f $command)
        }
        elseif ($result.exit_code -eq 0) {
            update_status -text '命令执行成功' -tone 'ok'
            add_message_entry -direction 'SYS' -text ('命令成功(exit=0): {0}  输出末尾: {1}' -f $command, $tail)
        }
        else {
            update_status -text ('命令失败 exit={0}' -f $result.exit_code) -tone 'err'
            add_message_entry -direction 'SYS' -text ('命令失败(exit={0}): {1}  输出末尾: {2}' -f $result.exit_code, $command, $tail)
        }
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '执行失败', 'OK', 'Error') | Out-Null
    }
    finally {
        $script:gui_busy = $false
        set_main_button_enabled -button $button_flash -enabled $true
        $button_flash.Text = '执行并抓取'
    }
})

$button_hit_clear.Add_Click({
    set_hit_pattern -pattern ''
    add_message_entry -direction 'SYS' -text '已清除 RX 命中高亮。'
})

$button_connect.Add_Click({
    # 按钮始终 Enabled（禁用态在深底上不可读，见 set_main_button_enabled），
    # 互斥语义靠状态位守卫
    if ($script:connected) {
        return
    }
    try {
        connect_serial_port
    }
    catch {
        close_serial_port
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '连接失败', 'OK', 'Error') | Out-Null
    }
})

$button_disconnect.Add_Click({
    if (-not $script:connected) {
        return
    }
    close_serial_port
})

$button_clear.Add_Click({
    clear_message_entries
    add_message_entry -direction 'SYS' -text '消息记录已清空。'
})

$button_save.Add_Click({
    try {
        $default_name = 'serial_export_{0}.txt' -f (Get-Date -Format 'yyyyMMdd_HHmmss')
        $dialog = New-Object System.Windows.Forms.SaveFileDialog
        $dialog.Filter = '文本日志 (*.txt)|*.txt|所有文件 (*.*)|*.*'
        $dialog.FileName = $default_name
        $dialog.InitialDirectory = (Join-Path -Path $PSScriptRoot -ChildPath 'logs')
        if (-not (Test-Path -LiteralPath $dialog.InitialDirectory)) {
            New-Item -ItemType Directory -Path $dialog.InitialDirectory | Out-Null
        }

        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            flush_pending_display_entries -DrainAll
            flush_pending_log_blocks
            [System.IO.File]::WriteAllText($dialog.FileName, (get_log_export_text), [System.Text.Encoding]::UTF8)
            add_message_entry -direction 'SYS' -text ("日志已导出到:`r`n{0}" -f $dialog.FileName)
        }
    }
    catch {
        write_runtime_log -message ('导出日志失败: {0}' -f $_.Exception.Message)
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '导出失败', 'OK', 'Error') | Out-Null
    }
})

$button_send.Add_Click({
    try {
        send_current_text
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '发送失败', 'OK', 'Error') | Out-Null
    }
})

$textbox_send.Add_KeyDown({
    param($sender, $event_args)
    if ($event_args.Control -and $event_args.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $event_args.SuppressKeyPress = $true
        $button_send.PerformClick()
        return
    }

    # cmd 风格历史回翻：只在第一行 ⬆ / 最后一行 ⬇ 生效，
    # 多行编辑时方向键仍是移动光标
    if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Up) {
        if ($textbox_send.GetLineFromCharIndex($textbox_send.SelectionStart) -eq 0) {
            $event_args.SuppressKeyPress = $true
            [void](recall_send_history -direction -1)
        }
    }
    elseif ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Down) {
        $caret_line = $textbox_send.GetLineFromCharIndex($textbox_send.SelectionStart)
        $line_count = 1
        try {
            $line_count = [Math]::Max(1, $textbox_send.Lines.Count)
        }
        catch {
        }
        if ($caret_line -ge ($line_count - 1)) {
            $event_args.SuppressKeyPress = $true
            [void](recall_send_history -direction 1)
        }
    }
})

$button_send_file.Add_Click({
    if ($script:file_send_state) {
        # 发送中再点一次 = 停止
        stop_serial_file_send -reason '用户停止'
        update_status -text '文件发送已停止。' -tone 'info'
        return
    }

    try {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = '选择要发送的文件（按原样字节流发送）'
        $dialog.Filter = '所有文件 (*.*)|*.*'
        if ($dialog.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
            start_serial_file_send -path $dialog.FileName
        }
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '文件发送失败', 'OK', 'Error') | Out-Null
    }
})

# 文件发送按钮说明（ToolTip 挂脚本变量防 GC）
$tooltip_main = New-Object System.Windows.Forms.ToolTip
$tooltip_main.SetToolTip($button_send_file, '选择文件按原样字节流分块发送（大文件不卡界面）；发送中再点一次停止。换行/HEX 设置对文件发送不生效')

# 主窗体控件提示：悬停即知功能；全文索引在「帮助」按钮的《功能速览》里
$tooltip_main.SetToolTip($button_wave, '打开波形窗口：FireWater / JustFloat / RawData 实时曲线、频谱、XY 相位图（IQ 星座）；Modbus RTU 调试台与频谱仪都在页签里')
$tooltip_main.SetToolTip($button_help, '功能速览：全部功能点与快捷键一览')
$tooltip_main.SetToolTip($button_connect, '按当前参数打开串口并开始接收')
$tooltip_main.SetToolTip($button_refresh, '重新扫描系统串口；勾选「新设备自动连接」后插入设备即自动连上')
$tooltip_main.SetToolTip($textbox_rx, 'RX 日志：悬停任意行 0.4 秒自动 HEX⇄文本/十进制/float32 互转；Ctrl+F 查找与时间戳定位')
$tooltip_main.SetToolTip($textbox_tx, 'TX 日志：悬停行查看该发送内容的 UTF-8 HEX')
$tooltip_main.SetToolTip($textbox_send, '输入要发送的内容；⬆/⬇ 像 cmd 一样回翻发送历史，⬇ 走到底恢复正在编辑的草稿')
$tooltip_main.SetToolTip($button_send, '发送输入框内容（发送格式为 HEX 时按十六进制解析）')
$tooltip_main.SetToolTip($checkbox_append_newline, '发送时在末尾追加所选换行符（CR / LF / CRLF）')
$tooltip_main.SetToolTip($button_clear, '清空 RX / TX 日志显示')
$tooltip_main.SetToolTip($button_save, '把当前 RX 日志导出为文件')
$tooltip_main.SetToolTip($script:find_box, '输入关键字：输入即匹配并高亮全部命中、行自动居中；输入 20:57 这类时间戳则直接跳到该时刻的日志。Enter 下一个 / Shift+Enter 上一个 / Esc 关闭')

function show_help_dialog {
    # 功能速览：给新用户的单页功能索引（配色跟随当前主题；只读，Esc / 关闭按钮退出）
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = '功能速览 — RK3562 串口收发工具 V6'
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false
    $dlg.MinimizeBox = $false
    $dlg.ShowInTaskbar = $false
    $dlg.StartPosition = 'CenterParent'
    $dlg.Size = New-Object System.Drawing.Size(720, 640)
    $dlg.BackColor = $script:ui.PanelBg
    $dlg.Font = new_font -name 'Microsoft YaHei UI' -size 9

    $help_lines = @(
        '【收发与日志】',
        '  • ASCII / HEX 双格式收发，换行符可选 CR / LF / CRLF',
        '  • RX / TX 双栏日志自动按 7:3 分配，可自动保存到 logs 目录、一键导出',
        '  • 悬停任意日志行 0.4 秒：HEX 行自动解码为 文本(UTF-8) / 十进制 / float32，文本行给出 HEX',
        '',
        '【查找与定位】（Ctrl+F）',
        '  • 输入即搜：打字过程实时匹配，全部命中弱色高亮 + 当前命中强色高亮，行自动滚到视口中间',
        '  • Enter / Shift+Enter（或 F3 / Shift+F3）向后 / 向前循环跳转，到头自动回绕，不分大小写',
        '  • 直接输入时间戳（如 20:57:38 或 2026-10-24 20:57:38）跳到第一条 ≥ 该时刻的日志',
        '',
        '【发送增强】',
        '  • ⬆ / ⬇ 像 cmd 一样回翻发送历史（上限 200 条，⬇ 走到底恢复正在编辑的草稿）',
        '  • 文件发送：任意文件按 4KiB 分块流式发送，大文件不卡界面，发送中可随时停止',
        '',
        '【波形示波】（点「波形窗口」）',
        '  • FireWater / JustFloat / RawData 三协议实时曲线 + 频谱插件 + XY 相位图（IQ 星座图）',
        '  • 无设备时点「模拟数据源」验证整条链路；曲线可导出 PNG / CSV / TXT',
        '  • 协议格式或波特率不匹配时，状态条显示缓冲「积压」增长并变色提醒（黄→红）',
        '',
        '【Modbus RTU 插件】（波形窗口 → 「Modbus」页签，无需另开窗口）',
        '  • 事务页：功能码 01/02/03/04/05/06/0F/10，CRC16 自动生成与校验，异常码中文说明，',
        '    寄存器解码支持 HEX / 十进制 / 有符号 / float32(ABCD / CDAB)',
        '  • 实时监视页：把读地址加入 Watch 表，合并轮询，数值变化闪色（涨绿 / 跌红），双击行回填事务区',
        '',
        '【快捷键】',
        '  Ctrl+F 查找栏    Enter 下一个    Shift+Enter 上一个    F3 / Shift+F3 循环跳转',
        '  Esc 关闭查找栏    ⬆ / ⬇ 发送历史',
        '',
        '详细文档见仓库 README · github.com/TZH-user/serialscope'
    )

    $body = New-Object DarkRichTextBox
    $body.Location = New-Object System.Drawing.Point(14, 12)
    $body.Size = New-Object System.Drawing.Size(676, 524)
    $body.Anchor = 'Top,Bottom,Left,Right'
    $body.BorderStyle = 'None'
    $body.ReadOnly = $true
    $body.BackColor = $script:ui.PanelBg
    $body.ForeColor = $script:ui.TextMain
    $body.Text = ($help_lines -join "`n")
    $body.EnableDarkScroll($true, $script:ui.PanelBg)
    # 段落头用信息蓝、尾行用弱色，正文用主前景色（\n 拼接，按行偏移上色）
    $offset = 0
    foreach ($line in $help_lines) {
        $body.SelectionStart = $offset
        $body.SelectionLength = $line.Length
        if ($line.StartsWith('【')) {
            $body.SelectionColor = $script:ui.InfoText
        }
        elseif ($line.StartsWith('详细文档')) {
            $body.SelectionColor = $script:ui.TextDim
        }
        else {
            $body.SelectionColor = $script:ui.TextMain
        }
        $offset += $line.Length + 1
    }
    $body.SelectionStart = 0
    $body.SelectionLength = 0
    $dlg.Controls.Add($body)

    $link_repo = New-Object System.Windows.Forms.LinkLabel
    $link_repo.Text = 'github.com/TZH-user/serialscope'
    $link_repo.Location = New-Object System.Drawing.Point(14, 556)
    $link_repo.Anchor = 'Bottom,Left'
    $link_repo.AutoSize = $true
    $link_repo.LinkColor = $script:ui.InfoText
    $link_repo.Add_LinkClicked({
        try {
            [System.Diagnostics.Process]::Start('https://github.com/TZH-user/serialscope')
        }
        catch {
        }
    })
    $dlg.Controls.Add($link_repo)

    $btn_update = New-Object System.Windows.Forms.Button
    $btn_update.Text = '检查更新'
    $btn_update.Location = New-Object System.Drawing.Point(496, 552)
    $btn_update.Size = New-Object System.Drawing.Size(96, 30)
    $btn_update.Anchor = 'Bottom,Right'
    $dlg.Controls.Add($btn_update)
    style_main_button -button $btn_update
    $btn_update.Add_Click({
        $btn_update.Enabled = $false
        $btn_update.Text = '检查中…'
        [System.Windows.Forms.Application]::DoEvents()
        $r = check_app_update -current_version $script:app_version
        $btn_update.Enabled = $true
        $btn_update.Text = '检查更新'
        if (-not $r.Ok) {
            [System.Windows.Forms.MessageBox]::Show(('检查失败（网络/代理不可达）：{0}' -f $r.Note), '检查更新', 'OK', 'Warning') | Out-Null
            return
        }
        if ($r.HasUpdate) {
            $choice = [System.Windows.Forms.MessageBox]::Show(('当前 {0}，最新 {1}。去下载页？' -f $r.Current, $r.Latest), '发现新版本', 'YesNo', 'Information')
            if ($choice -eq [System.Windows.Forms.DialogResult]::Yes) {
                try { [System.Diagnostics.Process]::Start($r.PageUrl) } catch { }
            }
        }
        else {
            [System.Windows.Forms.MessageBox]::Show(('已是最新（当前 {0}，最新 {1}）' -f $r.Current, $r.Latest), '检查更新', 'OK', 'Information') | Out-Null
        }
    })

    $btn_close = New-Object System.Windows.Forms.Button
    $btn_close.Text = '关闭'
    $btn_close.Location = New-Object System.Drawing.Point(598, 552)
    $btn_close.Size = New-Object System.Drawing.Size(92, 30)
    $btn_close.Anchor = 'Bottom,Right'
    $dlg.Controls.Add($btn_close)
    style_main_button -button $btn_close
    $dlg.CancelButton = $btn_close
    $btn_close.Add_Click({ $dlg.Close() })

    $dlg.Add_Shown({
        try {
            [DwmDark]::EnableDarkTitleBar($dlg.Handle, $true)
        }
        catch {
        }
    })
    [void]$dlg.ShowDialog($form)
    $dlg.Dispose()
}

$button_help.Add_Click({ show_help_dialog })

function capture_window_png {
    # 演示截图：PrintWindow(PW_RENDERFULLCONTENT) 渲染窗口自身像素。
    # 不用 CopyFromScreen——它要求窗口在前台，否则会把屏幕上无关窗口拍进去。
    param([System.Windows.Forms.Form]$win, [string]$path)

    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 300
    $rect = New-Object V6Native+RECT
    [void][V6Native]::GetWindowRect($win.Handle, [ref]$rect)
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top
    if ($w -le 0 -or $h -le 0) {
        throw ('window has no size: {0}' -f $win.Text)
    }
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [void][V6Native]::PrintWindow($win.Handle, $hdc, 2)
    $g.ReleaseHdc($hdc)
    $g.Dispose()
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Path $dir -Force)
    }
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output ('DEMO_SHOT {0} {1}x{2}' -f $path, $w, $h)
}

function inject_demo_log_data {
    # 演示截图：注入一段仿真的 RK3562 收发日志（时间戳回溯，观感自然），
    # 再打开查找栏并命中「version」，让高亮与计数标签一起入镜
    $t0 = (Get-Date).AddMinutes(-3)
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(10) -text 'RK3562 bootloader v1.2 ready'
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(12) -text '55 AA 10 00 20 24 1C'
    add_message_entry -direction 'TX' -timestamp $t0.AddSeconds(30) -text 'AT+VERSION?'
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(31) -text '+VERSION: fw_version=2.6.1 build 20261020'
    add_message_entry -direction 'TX' -timestamp $t0.AddSeconds(60) -text 'AT+CPU?'
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(61) -text '+CPU: load=23% temp=41.5C vbat=3.91V'
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(90) -text '48 65 6C 6C 6F 20 52 4B 33 35 36 32'
    add_message_entry -direction 'TX' -timestamp $t0.AddSeconds(120) -text 'AT+GPIO=1,1'
    add_message_entry -direction 'RX' -timestamp $t0.AddSeconds(121) -text 'OK'
    add_message_entry -direction 'SYS' -timestamp $t0.AddSeconds(140) -text 'Modbus RTU 插件已加载：事务调试 + 实时监视 Watch'
    flush_pending_display_entries -DrainAll
    [System.Windows.Forms.Application]::DoEvents()
    toggle_find_panel
    $script:find_box.Text = 'version'
    invoke_log_find
    $script:find_box.Focus()
}

$form.Add_Shown({
    start_ui_flush_timer
    # 启动自动检查更新（24h 节流，静默：只在有新版本时给一条 SYS；失败完全无感）
    $last_check_raw = read_gui_setting -key 'update_last_check'
    $last_check = [long]0
    if ([long]::TryParse($last_check_raw, [ref]$last_check) -and
        (([DateTimeOffset]::Now.ToUnixTimeSeconds() - $last_check) -lt 86400)) {
        # 24 小时内查过，跳过
    }
    else {
        save_gui_setting -key 'update_last_check' -value ([string][DateTimeOffset]::Now.ToUnixTimeSeconds())
        $update_timer = New-Object System.Windows.Forms.Timer
        $update_timer.Interval = 8000
        $update_timer.Add_Tick({
            $update_timer.Stop()
            $update_timer.Dispose()
            $r = check_app_update -current_version $script:app_version
            if ($r.Ok -and $r.HasUpdate) {
                add_message_entry -direction 'SYS' -text ('发现新版本 {0}（当前 {1}）：帮助 → 检查更新 可前往下载。' -f $r.Latest, $r.Current)
                flush_pending_display_entries
            }
        })
        $update_timer.Start()
    }
    # 日志区比例：写死 SplitterDistance 在窗口放大后会让 TX 区吃掉全部增量，
    # 这里按当前宽度重算一次 7:3（用户之后仍可手动拖动分隔条）
    if ($split_logs.Width -gt 0) {
        $split_logs.SplitterDistance = [int]($split_logs.Width * 0.7)
    }
    load_gui_settings
    refresh_ports
    start_port_watch_timer
    if ($ShotTheme) {
        $combobox_theme.SelectedItem = $ShotTheme
    }
    apply_theme -theme_name ([string]$combobox_theme.SelectedItem)
    if (-not $NoControlPort) {
        start_control_listener -port_number $ControlPort
    }
    add_message_entry -direction 'SYS' -text '工具已启动。V6 支持三种协议实时波形（点「格式说明」查看格式）；无头捕获与 AI 控制通道用法见 README_V5.md。RK3562 推荐先试 1500000 / 8 / None / One / None。'

    if ($ShotPath) {
        # 主题回归钩子：等界面稳定 + 消息刷完再截，截完自动退出。
        # 必须在消息循环里做（DrawToBitmap 依赖已建立的绘制区域）。
        # 注意：-ShotPath 请传【纯 ASCII 路径】。用 -File 起子进程时命令行里的中文
        # 会被按 ANSI 解码成乱码，路径落空且不报错。
        $script:shot_timer = New-Object System.Windows.Forms.Timer
        $script:shot_timer.Interval = 1200
        $script:shot_target = $ShotPath
        $script:shot_audit = [System.IO.Path]::ChangeExtension($ShotPath, '.themeaudit.txt')
        $script:shot_timer.Add_Tick({
            $script:shot_timer.Stop()
            [System.IO.File]::AppendAllText(
                [System.IO.Path]::ChangeExtension($ShotPath, '.breadcrumb.txt'),
                ('tick fired' + "`r`n"),
                [System.Text.UTF8Encoding]::new($false))
            try {
                flush_pending_display_entries -DrainAll
                [System.Windows.Forms.Application]::DoEvents()
                write_theme_audit -path $script:shot_audit
                if ($ShotMode -eq 'demo') {
                    inject_demo_log_data
                }
                if ($ShotMode -eq 'demo') {
                    capture_window_png -win $form -path $script:shot_target
                }
                else {
                    $bounds = $form.Bounds
                    $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
                    if ($ShotMode -eq 'screen') {
                        # 真实抓屏：DrawToBitmap 对 ComboBox/滚动条/复选框一律渲染成
                        # 纯白块或纯黑块，完全不可信；只有抓屏能拍到它们的真实像素。
                        # 代价是窗口必须真的在屏幕上且不被遮挡，所以先置顶。
                        $form.TopMost = $true
                        $form.Activate()
                        [System.Windows.Forms.Application]::DoEvents()
                        Start-Sleep -Milliseconds 500
                        $bounds = $form.Bounds
                        $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
                        $g = [System.Drawing.Graphics]::FromImage($bmp)
                        $g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, (New-Object System.Drawing.Size($bounds.Width, $bounds.Height)))
                        $g.Dispose()
                    }
                    else {
                        $g = [System.Drawing.Graphics]::FromImage($bmp)
                        $form.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $bounds.Width, $bounds.Height)))
                        $g.Dispose()
                    }
                    $dir = Split-Path -Parent $script:shot_target
                    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                        [void](New-Item -ItemType Directory -Path $dir -Force)
                    }
                    $bmp.Save($script:shot_target, [System.Drawing.Imaging.ImageFormat]::Png)
                    $bmp.Dispose()
                    [System.IO.File]::AppendAllText(
                        [System.IO.Path]::ChangeExtension($ShotPath, '.breadcrumb.txt'),
                        ('png saved: ' + (Test-Path -LiteralPath $script:shot_target) + "`r`n"),
                        [System.Text.UTF8Encoding]::new($false))
                    Write-Output ('SHOT_SAVED {0} {1}x{2}' -f $script:shot_target, $bounds.Width, $bounds.Height)
                }
                if ($ShotMode -eq 'demo') {
                    # 演示模式续拍：波形窗（FireWater 三通道正弦）+ Modbus 插件（预填 Watch 表的监视页）
                    try {
                        open_wave_window
                        $script:wave_protocol = 'FireWater'
                        reset_wave_buffers
                        reset_wave_decoder
                        for ($i = 0; $i -lt 420; $i++) {
                            $demo_line = ('{0},{1},{2}' -f ([Math]::Sin($i / 15.0) * 2.0), ([Math]::Cos($i / 7.0)), (($i % 48) / 6.0)) + "`n"
                            append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes($demo_line))
                        }
                        for ($k = 0; $k -lt 18; $k++) {
                            [System.Windows.Forms.Application]::DoEvents()
                            Start-Sleep -Milliseconds 40
                        }
                        capture_window_png -win $script:wave_form -path ([System.IO.Path]::ChangeExtension($script:shot_target, '.wave.png'))

                        if (-not (Get-Command show_modbus_panel -ErrorAction SilentlyContinue)) {
                            . (Join-Path $script:app_root 'plugins\modbus_rtu\plugin.ps1')
                            load_wave_plugins
                        }
                        # 页签模式：切入「Modbus」页（懒构建），预填 Watch 表后抓波形窗口整窗
                        $modbus_tab_index = -1
                        for ($ti = 0; $ti -lt $script:wave_tabs.Controls.Count; $ti++) {
                            if ($script:wave_tabs.Controls[$ti].Text -eq 'Modbus') { $modbus_tab_index = $ti; break }
                        }
                        if ($modbus_tab_index -lt 0) { throw 'demo: Modbus tab not found' }
                        $script:wave_tabs.SelectedIndex = $modbus_tab_index
                        for ($k = 0; $k -lt 10; $k++) {
                            [System.Windows.Forms.Application]::DoEvents()
                            Start-Sleep -Milliseconds 40
                        }
                        $demo_watch = @(
                            @{ Name = '温度传感'; Slave = 1; Fc = 3; Start = 0; Count = 2; Decode = '十进制'; LastValue = '41.5, 3.91'; LastStatus = 'OK' },
                            @{ Name = 'CPU 负载'; Slave = 1; Fc = 3; Start = 10; Count = 1; Decode = '十进制'; LastValue = '23'; LastStatus = 'OK' },
                            @{ Name = '输出线圈'; Slave = 1; Fc = 1; Start = 0; Count = 8; Decode = '十进制'; LastValue = '1,0,1'; LastStatus = 'OK' }
                        )
                        foreach ($d in $demo_watch) {
                            $demo_item = [pscustomobject]@{
                                Key = (get_modbus_watch_key -slave $d.Slave -fc $d.Fc -start $d.Start)
                                Name = $d.Name
                                Slave = $d.Slave
                                Fc = $d.Fc
                                Start = $d.Start
                                Count = $d.Count
                                Decode = $d.Decode
                                LastValue = $d.LastValue
                                LastStatus = $d.LastStatus
                            }
                            [void](upsert_modbus_watch_item -list $script:modbus_watch_items -item $demo_item)
                        }
                        rebuild_modbus_watch_grid
                        $script:modbus_tab_ctrl.SelectedIndex = 1
                        [System.Windows.Forms.Application]::DoEvents()
                        Start-Sleep -Milliseconds 400
                        capture_window_png -win $script:wave_form -path ([System.IO.Path]::ChangeExtension($script:shot_target, '.modbus.png'))
                        $script:wave_tabs.SelectedIndex = 0
                    }
                    catch {
                        Write-Output ('DEMO_FAIL {0}' -f $_.Exception.Message)
                    }
                }
            }
            catch {
                [System.IO.File]::AppendAllText(
                    [System.IO.Path]::ChangeExtension($ShotPath, '.breadcrumb.txt'),
                    ('FAIL: ' + $_.Exception.Message + "`r`n"),
                    [System.Text.UTF8Encoding]::new($false))
                Write-Output ('SHOT_FAIL ' + $_.Exception.Message)
            }
            $form.Close()
        })
        $script:shot_timer.Start()
    }
})

$form.Add_FormClosing({
    save_gui_session
    stop_control_listener
    stop_session_record
    stop_port_watch_timer
    if ($script:wave_form -and -not $script:wave_form.IsDisposed) {
        $script:wave_form.Close()
    }
    close_serial_port
    flush_pending_display_entries -DrainAll
    stop_ui_flush_timer
})

if ($WaveTest) {
    # 端到端测试：构建真实窗体与波形窗口，从接收管线注入合成数据并驱动渲染
    $form.Show()
    open_wave_window

    # 查找栏：构建即隐藏，开关函数可用（Ctrl+F 键盘路径无法无头驱动，逻辑在 SelfTest 覆盖）
    if (-not $script:find_panel -or $script:find_panel.IsDisposed) { throw 'find panel missing' }
    if ($script:find_panel.Visible) { throw 'find panel should start hidden' }
    toggle_find_panel
    if (-not $script:find_panel.Visible) { throw 'find panel toggle failed' }

    # 真实链路：注入两行标记 → 输入即搜（live 计时器路径）→ 导航后不被拽回首处
    append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes('findme_marker_alpha' + "`n"))
    append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes('findme_marker_beta' + "`n"))
    flush_pending_display_entries -DrainAll
    $script:find_box.Text = 'findme_marker'
    # 泵消息 360ms：让 150ms 防抖 tick 真实走完（live 搜索路径）
    for ($k = 0; $k -lt 6; $k++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 60
    }
    if ($script:find_label_count.Text -ne '第 1/2 处') { throw ('live search count wrong: ' + $script:find_label_count.Text) }
    if ($textbox_rx.SelectionLength -le 0) { throw 'find did not select any match' }
    if ($script:find_highlight_ranges.Count -le 0) { throw 'find highlight missing' }
    $live_sel = $textbox_rx.SelectionStart

    invoke_log_find
    if ($script:find_label_count.Text -ne '第 2/2 处') { throw ('find next count wrong: ' + $script:find_label_count.Text) }
    $next_sel = $textbox_rx.SelectionStart
    if ($next_sel -le $live_sel) { throw 'find next did not advance' }

    # 关键回归：导航后再泵 360ms（>2 个防抖周期），不得被 live 计时器拽回首处
    for ($k = 0; $k -lt 6; $k++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 60
    }
    if ($script:find_label_count.Text -ne '第 2/2 处') { throw 'live timer yanked count back after navigation' }
    if ($textbox_rx.SelectionStart -ne $next_sel) { throw 'live timer yanked selection back after navigation' }

    invoke_log_find -backward:$true
    if ($script:find_label_count.Text -ne '第 1/2 处') { throw 'find backward did not return to first' }
    Write-Output 'WAVETEST find bar OK'

    # 空闲落显端到端：无换行残行静默超阈值 → 自动落成 RX 条目并清空缓冲；新鲜字节不触发
    $before_entries = $script:message_entries.Count
    $script:rx_line_buffer = 'idle_flush_tail_no_newline'
    $script:rx_last_byte_at = (Get-Date).AddSeconds(-2)
    if (-not (flush_rx_line_if_idle)) { throw 'idle flush did not fire' }
    if ($script:rx_line_buffer -ne '') { throw 'idle flush left residue' }
    if ($script:message_entries.Count -ne ($before_entries + 1)) { throw 'idle flush entry missing' }
    if ($script:message_entries[$script:message_entries.Count - 1].Text -ne 'idle_flush_tail_no_newline') { throw 'idle flush entry text wrong' }
    $script:rx_line_buffer = 'fresh_tail'
    $script:rx_last_byte_at = [datetime]::Now
    if (flush_rx_line_if_idle) { throw 'idle flush fired on fresh bytes' }
    $script:rx_line_buffer = ''
    Write-Output 'WAVETEST idle flush OK'

    # JustFloat / RawData 协议字节流往返
    $script:wave_protocol = 'JustFloat'
    reset_wave_buffers
    reset_wave_decoder
    $jf_frame = New-Object byte[] 16
    [BitConverter]::GetBytes([float]1.5).CopyTo($jf_frame, 0)
    [BitConverter]::GetBytes([float](-2.25)).CopyTo($jf_frame, 4)
    [BitConverter]::GetBytes([float]3.75).CopyTo($jf_frame, 8)
    $jf_tail = [byte[]](0, 0, 0x80, 0x7F)
    $jf_tail.CopyTo($jf_frame, 12)
    try_consume_wave_bytes -bytes ([byte[]]@($jf_frame[0..5]))
    try_consume_wave_bytes -bytes ([byte[]]@($jf_frame[6..15]))
    $jf_ok = ($script:wave_frame_count -eq 1 -and $script:wave_ch_values[2][0] -eq 3.75)

    $script:wave_protocol = 'RawData'
    reset_wave_buffers
    reset_wave_decoder
    try_consume_wave_bytes -bytes ([byte[]](5, 250, 128))
    $raw_ok = ($script:wave_frame_count -eq 3 -and $script:wave_ch_values[0][2] -eq 128)

    $script:wave_protocol = 'FireWater'
    reset_wave_buffers
    reset_wave_decoder

    # FireWater 文本行经字节级接收管线注入
    for ($i = 0; $i -lt 500; $i++) {
        $line_text = ('{0},{1},{2}' -f ([Math]::Sin($i / 15.0) * 2.0), ([Math]::Cos($i / 7.0)), ($i % 5)) + "`n"
        append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes($line_text))
    }
    # 混入文本行与含 ANSI 序列的数据行
    append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes('spec:hello world' + "`n"))
    append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes([string][char]27 + '[32m-0.25,9.5,1' + [string][char]27 + '[0m' + "`n"))

    for ($k = 0; $k -lt 12; $k++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 40
    }

    $wave_ok = ($script:wave_frame_total -eq 501 -and $script:wave_ch_values.Count -eq 3 -and $jf_ok -and $raw_ok -and $script:wave_form -and -not $script:wave_form.IsDisposed)
    if (-not $wave_ok) {
        Write-Output ('WAVETEST_FAIL frames={0} channels={1} jf={2} raw={3}' -f $script:wave_frame_total, $script:wave_ch_values.Count, $jf_ok, $raw_ok)
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 模拟数据源测试：启动 ~0.6s 应产生额外帧
    start_wave_sim
    for ($k = 0; $k -lt 15; $k++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 40
    }
    stop_wave_sim

    if (-not ($script:wave_frame_total -gt 501 -and (-not $script:wave_sim_on))) {
        Write-Output ('WAVETEST_FAIL sim frames={0}' -f $script:wave_frame_total)
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 导出功能测试：PNG / CSV / TXT，选中通道 0 和 2
    $export_dir = Join-Path $env:TEMP ('wave_v6_test_{0}' -f (Get-Date -Format 'yyyyMMddHHmmss'))
    New-Item -ItemType Directory -Path $export_dir | Out-Null
    $export_ok = $true
    try {
        export_wave_to_file -path (Join-Path $export_dir 't.png') -format 'png' -channel_indices @(0, 2)
        export_wave_to_file -path (Join-Path $export_dir 't.csv') -format 'csv' -channel_indices @(0, 2)
        export_wave_to_file -path (Join-Path $export_dir 't.txt') -format 'txt' -channel_indices @(0, 2)
        $csv_lines = [System.IO.File]::ReadAllLines((Join-Path $export_dir 't.csv'))
        $png_size = (Get-Item -LiteralPath (Join-Path $export_dir 't.png')).Length
        $txt_size = (Get-Item -LiteralPath (Join-Path $export_dir 't.txt')).Length
        # 表头用显示名（用户可能已重命名通道），动态构建期望值
        $expected_header = 'index,' + (get_wave_channel_display_name -index 0) + ',' + (get_wave_channel_display_name -index 2)
        $export_ok = ($png_size -gt 0 -and $txt_size -gt 0 -and $csv_lines[0] -eq $expected_header -and $csv_lines.Count -gt 10)
    }
    catch {
        $export_ok = $false
        Write-Output ('WAVETEST export exception: {0}' -f $_.Exception.Message)
    }

    # 默认导出路径记忆测试（写入-读回-清理）
    $default_roundtrip = $false
    try {
        $setting_dir = Join-Path $env:TEMP ('wave_v6_default_{0}' -f (Get-Date -Format 'yyyyMMddHHmmssfff'))
        New-Item -ItemType Directory -Path $setting_dir | Out-Null
        set_wave_export_default_dir -directory $setting_dir
        if ((get_wave_export_default_dir) -eq $setting_dir) {
            $default_roundtrip = $true
        }
        Remove-Item -LiteralPath $setting_dir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (get_wave_export_settings_path) -Force -ErrorAction SilentlyContinue
    }
    catch {
        Write-Output ('WAVETEST default-path exception: {0}' -f $_.Exception.Message)
    }
    $export_ok = ($export_ok -and $default_roundtrip)
    Remove-Item -LiteralPath $export_dir -Recurse -Force -ErrorAction SilentlyContinue

    if (-not $export_ok) {
        Write-Output 'WAVETEST_FAIL export'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 会话录制测试
    start_session_record
    append_received_bytes -bytes ([byte[]](1, 2, 3, 250))
    stop_session_record
    $record_ok = ($script:session_record_path -and (Test-Path -LiteralPath $script:session_record_path))
    if ($record_ok) {
        $record_first = Get-Content -LiteralPath $script:session_record_path -TotalCount 1 -Encoding UTF8
        $record_ok = ($record_first -match '"DATA"')
        Remove-Item -LiteralPath $script:session_record_path -Force -ErrorAction SilentlyContinue
    }
    $script:wbutton_record.Text = '录制'
    if (-not $record_ok) {
        Write-Output 'WAVETEST_FAIL record'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # AI 控制通道回环测试（含 set_hit_pattern 与 read_lines 的 hit 字段）
    start_control_listener -port_number 9111
    $control_ok = $false
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new('127.0.0.1', 9111)
        $tcp_stream = $tcp.GetStream()
        $multi_request = "{0}{1}{2}{1}{3}{1}" -f '{"cmd":"ping"}', [char]10, '{"cmd":"set_hit_pattern","pattern":"panic"}', '{"cmd":"set_hit_pattern","pattern":""}'
        $request_bytes = [System.Text.Encoding]::UTF8.GetBytes($multi_request)
        $tcp_stream.Write($request_bytes, 0, $request_bytes.Length)
        $response_text = ''
        $control_deadline = [datetime]::Now.AddSeconds(6)
        while ([datetime]::Now -lt $control_deadline) {
            [System.Windows.Forms.Application]::DoEvents()
            if ($tcp_stream.DataAvailable) {
                $resp_buf = New-Object byte[] 65536
                $resp_n = $tcp_stream.Read($resp_buf, 0, $resp_buf.Length)
                if ($resp_n -gt 0) {
                    $response_text += [System.Text.Encoding]::UTF8.GetString($resp_buf, 0, $resp_n)
                    if ((@([regex]::Matches($response_text, '"ok"\s*:\s*true'))).Count -ge 3) {
                        break
                    }
                }
            }
            Start-Sleep -Milliseconds 30
        }
        $tcp.Close()
        $control_ok = ((@([regex]::Matches($response_text, '"ok"\s*:\s*true'))).Count -ge 3)
    }
    catch {
        $control_ok = $false
        Write-Output ('WAVETEST control exception: {0}' -f $_.Exception.Message)
    }
    stop_control_listener
    if (-not $control_ok) {
        Write-Output 'WAVETEST_FAIL control'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 端口列表与设备描述：手动刷新后下拉项应与系统端口一致，显示文本可反解析出 COM 名
    refresh_ports
    $system_ports = @([System.IO.Ports.SerialPort]::GetPortNames())
    Write-Output ('WAVETEST ports: {0}' -f (($combobox_port.Items | ForEach-Object { $_ }) -join ' | '))
    if ($combobox_port.Items.Count -lt $system_ports.Count) {
        Write-Output 'WAVETEST_FAIL port_list'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }
    if ($combobox_port.Items.Count -gt 0) {
        $display_text = [string]$combobox_port.Items[$combobox_port.SelectedIndex]
        $parsed_port = get_port_name_from_display -text $display_text
        if ([string]::IsNullOrWhiteSpace($parsed_port) -or $system_ports -notcontains $parsed_port) {
            Write-Output 'WAVETEST_FAIL port_display_parse'
            if ($script:wave_form) { $script:wave_form.Close() }
            $form.Close()
            exit 1
        }
    }

    # 板卡操作行：默认收起（切换按钮 + 提示标签在状态行右端），可展开/收起
    $actions_ok = ($panel_top.Height -eq 82 -and $button_actions_toggle.Text -eq '+' -and $label_actions.Text -eq '板卡操作')
    $actions_ok = $actions_ok -and ($button_actions_toggle.Location.X -eq 1196 -and $label_actions.Location.X -eq 1126)
    set_actions_expanded -expanded $true
    $actions_ok = $actions_ok -and ($label_actions.Visible -and $panel_top.Height -eq 124 -and $button_actions_toggle.Text -eq '−')
    $actions_ok = $actions_ok -and ($button_actions_toggle.Location.X -eq 10 -and $button_actions_toggle.Location.Y -eq 86)
    set_actions_expanded -expanded $false
    $actions_ok = $actions_ok -and ($button_actions_toggle.Location.X -eq 1196 -and $button_actions_toggle.Location.Y -eq 46)
    if (-not $actions_ok) {
        Write-Output 'WAVETEST_FAIL actions_toggle'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 波形滑块面板与初始跟随视图
    $slider_ok = ($script:wave_slider_panel -and -not $script:wave_slider_panel.IsDisposed -and $script:wave_slider_panel.Height -eq 34)
    $slider_ok = $slider_ok -and ((get_wave_view_range -total ([Math]::Max(2, $script:wave_frame_count))).Follow)
    if (-not $slider_ok) {
        Write-Output 'WAVETEST_FAIL wave_slider'
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 插件页签化与滤波切换
    $plugin_tab_found = $false
    if ($script:wave_tabs -and -not $script:wave_tabs.IsDisposed) {
        foreach ($page in $script:wave_tabs.Controls) {
            if ($page.Text -eq '频谱') { $plugin_tab_found = $true; break }
        }
    }
    $filter_ok = ($script:wcombo_filter.SelectedItem -eq '关' -and $script:wave_filter_mode -eq '关')
    $script:wcombo_filter.SelectedItem = '滑动平均'
    $filter_ok = $filter_ok -and ($script:wave_filter_mode -eq '滑动平均')
    $filter_ok = $filter_ok -and (-not $script:wtext_filter_param.Enabled -eq $false)
    # 触发一次带滤波的重绘路径（模拟数据在跑，get_wave_view_data 应产出缓存）
    if ($script:wave_ch_values.Count -gt 0) {
        [void](get_wave_view_data -channel 0 -start 0 -end ([Math]::Min(100, $script:wave_frame_count)))
        $filter_ok = $filter_ok -and ($script:wave_filter_cache.ContainsKey(0))
    }
    $script:wcombo_filter.SelectedItem = '关'
    if (-not ($plugin_tab_found -and $filter_ok)) {
        Write-Output ('WAVETEST_FAIL plugin_or_filter (plugin_tab={0} filter_ok={1})' -f $plugin_tab_found, $filter_ok)
        if ($script:wave_form) { $script:wave_form.Close() }
        $form.Close()
        exit 1
    }

    # 视觉自查：波形窗口整窗截图（审阅配色/图标/填充效果，不影响断言）
    # -ShotMode screen 用 CopyFromScreen 抓真实像素：DrawToBitmap 对 ComboBox 会走原生
    # 渲染（无视 DarkComboBox.OnPaint 自绘箭头），拍出来是浅色假象，无法验证下拉箭头。
    try {
        $preview_path = Join-Path $PSScriptRoot 'logs\wave_preview.png'
        $wf = $script:wave_form
        $wf.TopMost = $true
        $wf.BringToFront()
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 400
        $bmp_prev = New-Object System.Drawing.Bitmap([Math]::Max(1, $wf.Width), [Math]::Max(1, $wf.Height))
        if ($ShotMode -eq 'screen') {
            $bounds = [System.Drawing.Rectangle]::FromLTRB($wf.Left, $wf.Top, $wf.Right, $wf.Bottom)
            $gp = [System.Drawing.Graphics]::FromImage($bmp_prev)
            $gp.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, (New-Object System.Drawing.Size($bounds.Width, $bounds.Height)))
            $gp.Dispose()
        }
        else {
            $wf.DrawToBitmap($bmp_prev, (New-Object System.Drawing.Rectangle(0, 0, $bmp_prev.Width, $bmp_prev.Height)))
        }
        $bmp_prev.Save($preview_path, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp_prev.Dispose()
        Write-Output ('WAVETEST preview: {0} (mode={1})' -f $preview_path, $ShotMode)
    }
    catch {
        Write-Output ('WAVETEST preview exception: {0}' -f $_.Exception.Message)
    }

    # 查找栏留档：主窗体当前状态（查找栏开启 + 命中选区）落 logs\find_preview.png
    try {
        [System.Windows.Forms.Application]::DoEvents()
        $find_path = Join-Path $PSScriptRoot 'logs\find_preview.png'
        $bmp_find = New-Object System.Drawing.Bitmap([Math]::Max(1, $form.Width), [Math]::Max(1, $form.Height))
        $form.DrawToBitmap($bmp_find, (New-Object System.Drawing.Rectangle(0, 0, $bmp_find.Width, $bmp_find.Height)))
        $bmp_find.Save($find_path, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp_find.Dispose()
        Write-Output ('WAVETEST find preview: {0}' -f $find_path)
        toggle_find_panel -show:$false
    }
    catch {
        Write-Output ('WAVETEST find preview exception: {0}' -f $_.Exception.Message)
    }

    $script:wave_form.Close()
    $form.Close()
    Write-Output 'WAVETEST_OK'
    exit 0
}

try {
    [void]$form.ShowDialog()
}
catch {
    write_runtime_log -message ('程序主循环异常退出: {0}' -f $_.Exception.Message)
    throw
}
