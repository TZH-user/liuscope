# ============================================================
# 频谱仪插件（spectrum v3）
# 对选中通道做汉宁窗 FFT：
#   - dB 刻度频谱（当前轨迹 / 峰值保持 / 指数平均）
#   - 参考电平 + dB/格 标度（y 轴顶=参考电平，10 格；瀑布映射跟随）
#   - RBW：自动（随点数）或手动（由采样率反推 FFT 长度），显示 Δf 与 ENBW
#   - RBW 与主频标注
#   - 瀑布图（时间-频率热图，最新帧在顶部）
#   - 鼠标 Marker 读数（频率 / dB）
# 计算核心 WaveDsp 由宿主 Add-Type 提供（C# 实时性能）。
#
# 插件规范（必须遵守，均为 StrictMode/作用域踩坑结论）：
#   1. 插件自身 $script: 状态变量必须在【顶层】预置 $null，
#      state 哈希表的每个键也必须预置（StrictMode 下访问缺失键即抛错）
#   2. 事件 handler（Paint/Tick/Click）里只访问 $script: 状态，
#      不得引用函数局部变量（handler 执行时局部作用域已销毁）
#   3. 绘制函数形参名必须与调用点参数名逐字一致
#      （普通函数对未知命名参数静默丢 $args，不会报错）
#   4. 重计算逻辑放下 C#（WaveDsp），PowerShell 只做胶水
# ============================================================

$script:spectrum_form = $null
$script:spectrum_state = $null

function show_spectrum_panel {
    # 合同 v2：传入 $container（波形窗口的插件页签）时内容长在页签里；不传则退回独立窗口
    param([System.Windows.Forms.Control]$container = $null)

    if ($container) {
        if ($script:spectrum_form -and -not $script:spectrum_form.IsDisposed) {
            return
        }
    }
    elseif ($script:spectrum_form -and -not $script:spectrum_form.IsDisposed) {
        $script:spectrum_form.Show()
        $script:spectrum_form.Activate()
        return
    }

    $form = $null
    if ($container) {
        $host_root = New-Object System.Windows.Forms.Panel
        $host_root.Dock = 'Fill'
        $host_root.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
        $host_root.Font = new_font -name 'Microsoft YaHei UI' -size 9
        [void]$container.Controls.Add($host_root)
    }
    else {
        $form = New-Object System.Windows.Forms.Form
        $host_root = $form
        $host_root.Text = '频谱仪 — 插件'
        $host_root.StartPosition = 'CenterParent'
        $host_root.Size = New-Object System.Drawing.Size(980, 660)
        $host_root.MinimumSize = New-Object System.Drawing.Size(760, 480)
        $host_root.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
        $host_root.Font = new_font -name 'Microsoft YaHei UI' -size 9
    }

    # ---- 顶部参数条 ----
    $toolbar = New-Object System.Windows.Forms.Panel
    $toolbar.Dock = 'Top'
    $toolbar.Height = 66
    $toolbar.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $label_ch = New-Object System.Windows.Forms.Label
    $label_ch.Text = '通道'
    $label_ch.Location = New-Object System.Drawing.Point(10, 12)
    $label_ch.AutoSize = $true
    $label_ch.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_ch)

    $combo_ch = New-Object System.Windows.Forms.ComboBox
    $combo_ch.Location = New-Object System.Drawing.Point(50, 8)
    $combo_ch.Size = New-Object System.Drawing.Size(76, 24)
    $combo_ch.DropDownStyle = 'DropDownList'
    for ($c = 0; $c -lt $script:wave_ch_values.Count; $c++) {
        [void]$combo_ch.Items.Add((get_wave_channel_display_name -index $c))
    }
    if ($combo_ch.Items.Count -gt 0) { $combo_ch.SelectedIndex = 0 }
    $toolbar.Controls.Add($combo_ch)

    $label_pts = New-Object System.Windows.Forms.Label
    $label_pts.Text = '点数'
    $label_pts.Location = New-Object System.Drawing.Point(136, 12)
    $label_pts.AutoSize = $true
    $label_pts.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_pts)

    $combo_pts = New-Object System.Windows.Forms.ComboBox
    $combo_pts.Location = New-Object System.Drawing.Point(176, 8)
    $combo_pts.Size = New-Object System.Drawing.Size(78, 24)
    $combo_pts.DropDownStyle = 'DropDownList'
    [void]$combo_pts.Items.AddRange(@('512', '1024', '2048', '4096', '8192'))
    $combo_pts.SelectedItem = '2048'
    $toolbar.Controls.Add($combo_pts)

    $label_fs = New-Object System.Windows.Forms.Label
    $label_fs.Text = '采样率(Hz)'
    $label_fs.Location = New-Object System.Drawing.Point(262, 12)
    $label_fs.AutoSize = $true
    $label_fs.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_fs)

    $text_fs = New-Object System.Windows.Forms.TextBox
    $text_fs.Location = New-Object System.Drawing.Point(338, 8)
    $text_fs.Size = New-Object System.Drawing.Size(76, 24)
    $text_fs.Text = '1000'
    $toolbar.Controls.Add($text_fs)

    $check_peak = New-Object System.Windows.Forms.CheckBox
    $check_peak.Text = '峰值保持'
    $check_peak.Location = New-Object System.Drawing.Point(424, 10)
    $check_peak.AutoSize = $true
    $check_peak.Checked = $true
    $check_peak.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $toolbar.Controls.Add($check_peak)

    $check_avg = New-Object System.Windows.Forms.CheckBox
    $check_avg.Text = '平均'
    $check_avg.Location = New-Object System.Drawing.Point(506, 10)
    $check_avg.AutoSize = $true
    $check_avg.Checked = $true
    $check_avg.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $toolbar.Controls.Add($check_avg)

    $button_clear_peak = New-Object System.Windows.Forms.Button
    $button_clear_peak.Text = '清峰值'
    $button_clear_peak.Location = New-Object System.Drawing.Point(560, 7)
    $button_clear_peak.Size = New-Object System.Drawing.Size(60, 24)
    style_wave_button -button $button_clear_peak
    $toolbar.Controls.Add($button_clear_peak)

    $check_wf = New-Object System.Windows.Forms.CheckBox
    $check_wf.Text = '瀑布图'
    $check_wf.Location = New-Object System.Drawing.Point(630, 10)
    $check_wf.AutoSize = $true
    $check_wf.Checked = $true
    $check_wf.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $toolbar.Controls.Add($check_wf)

    $check_auto = New-Object System.Windows.Forms.CheckBox
    $check_auto.Text = '自动刷新'
    $check_auto.Location = New-Object System.Drawing.Point(700, 10)
    $check_auto.AutoSize = $true
    $check_auto.Checked = $true
    $check_auto.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $toolbar.Controls.Add($check_auto)

    # 冻结：停止刷新，画面定格便于 Marker/截图分析（数据在主窗体缓冲继续/暂停由主窗体控制）
    $button_freeze = New-Object System.Windows.Forms.Button
    $button_freeze.Text = '冻结'
    $button_freeze.Location = New-Object System.Drawing.Point(782, 7)
    $button_freeze.Size = New-Object System.Drawing.Size(60, 24)
    style_wave_button -button $button_freeze
    $toolbar.Controls.Add($button_freeze)

    $label_peak = New-Object System.Windows.Forms.Label
    $label_peak.Location = New-Object System.Drawing.Point(620, 44)
    $label_peak.AutoSize = $true
    $label_peak.ForeColor = [System.Drawing.Color]::FromArgb(255, 200, 90)
    $toolbar.Controls.Add($label_peak)

    # ---- 第二行：显示标度设置（参考电平 / dB每格 / RBW）----
    $label_ref = New-Object System.Windows.Forms.Label
    $label_ref.Text = '参考电平'
    $label_ref.Location = New-Object System.Drawing.Point(10, 44)
    $label_ref.AutoSize = $true
    $label_ref.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_ref)

    $text_ref = New-Object System.Windows.Forms.TextBox
    $text_ref.Location = New-Object System.Drawing.Point(68, 40)
    $text_ref.Size = New-Object System.Drawing.Size(52, 24)
    $text_ref.Text = '0'
    $toolbar.Controls.Add($text_ref)

    $label_div = New-Object System.Windows.Forms.Label
    $label_div.Text = 'dB/格'
    $label_div.Location = New-Object System.Drawing.Point(130, 44)
    $label_div.AutoSize = $true
    $label_div.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_div)

    $combo_div = New-Object System.Windows.Forms.ComboBox
    $combo_div.Location = New-Object System.Drawing.Point(180, 40)
    $combo_div.Size = New-Object System.Drawing.Size(56, 24)
    $combo_div.DropDownStyle = 'DropDownList'
    [void]$combo_div.Items.AddRange(@('5', '10', '20', '50'))
    $combo_div.SelectedItem = '10'
    $toolbar.Controls.Add($combo_div)

    $label_rbw = New-Object System.Windows.Forms.Label
    $label_rbw.Text = 'RBW(Hz)'
    $label_rbw.Location = New-Object System.Drawing.Point(248, 44)
    $label_rbw.AutoSize = $true
    $label_rbw.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $toolbar.Controls.Add($label_rbw)

    $combo_rbw = New-Object System.Windows.Forms.ComboBox
    $combo_rbw.Location = New-Object System.Drawing.Point(312, 40)
    $combo_rbw.Size = New-Object System.Drawing.Size(76, 24)
    $combo_rbw.DropDownStyle = 'DropDownList'
    [void]$combo_rbw.Items.AddRange(@('自动', '0.05', '0.1', '0.2', '0.5', '1', '2', '5', '10', '20', '50', '100', '200', '500'))
    $combo_rbw.SelectedIndex = 0
    $toolbar.Controls.Add($combo_rbw)

    # 对数色标：dB→颜色前先做 t^0.5 感知增强，拉开噪声底附近的颜色层次；关闭则 dB 线性均分
    $check_log = New-Object System.Windows.Forms.CheckBox
    $check_log.Text = '对数色标'
    $check_log.Location = New-Object System.Drawing.Point(400, 40)
    $check_log.AutoSize = $true
    $check_log.Checked = $true
    $check_log.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $toolbar.Controls.Add($check_log)

    # ---- 频谱 / 瀑布 上下分屏 ----
    $split = New-Object System.Windows.Forms.SplitContainer
    $split.Dock = 'Fill'
    $split.Orientation = 'Horizontal'
    $split.SplitterDistance = 300
    $split.Panel1.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $split.Panel2.BackColor = [System.Drawing.Color]::FromArgb(16, 19, 26)

    $spec_panel = New-Object System.Windows.Forms.Panel
    $spec_panel.Dock = 'Fill'
    $spec_panel.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    # 双缓冲：否则每次 Marker 移动触发重绘时先整面擦背景再画，瞬间露底导致闪烁（同主波形窗口 4697 行）
    $spec_panel_db = $spec_panel.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'Instance,NonPublic')
    if ($spec_panel_db) {
        $spec_panel_db.SetValue($spec_panel, $true, $null)
    }
    $split.Panel1.Controls.Add($spec_panel)

    $wf_panel = New-Object System.Windows.Forms.Panel
    $wf_panel.Dock = 'Fill'
    $wf_panel.BackColor = [System.Drawing.Color]::FromArgb(16, 19, 26)
    $wf_panel_db = $wf_panel.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'Instance,NonPublic')
    if ($wf_panel_db) {
        $wf_panel_db.SetValue($wf_panel, $true, $null)
    }
    $split.Panel2.Controls.Add($wf_panel)

    $host_root.Controls.Add($split)
    $host_root.Controls.Add($toolbar)

    # ---- 状态（全部经 $script:spectrum_state，handler 只访问它）----
    $script:spectrum_state = @{
        Form = $form
        SpecPanel = $spec_panel
        WfPanel = $wf_panel
        Split = $split
        ComboCh = $combo_ch
        ComboPts = $combo_pts
        TextFs = $text_fs
        CheckPeak = $check_peak
        CheckAvg = $check_avg
        CheckWf = $check_wf
        CheckAuto = $check_auto
        TextRef = $text_ref
        ComboDiv = $combo_div
        ComboRbw = $combo_rbw
        CheckLog = $check_log
        ButtonFreeze = $button_freeze
        Frozen = $false
        PeakLabel = $label_peak
        MaxHold = $null          # double[] dB 峰值保持
        AvgDb = $null            # double[] dB 指数平均
        Waterfall = New-Object 'System.Collections.Generic.List[object]'   # List<byte[]>，0 = 最新
        WfBins = 0
        MarkerX = $null          # double? 频谱区鼠标 x（像素）
        LastSpectrumKey = ''
        LastAnalysis = $null     # @{Ver;Result} 分析缓存（StrictMode 下所有键必须预置）
        SceneBmp = $null         # 场景缓存位图（网格+轨迹），Marker 移动只 blit 不重画
        SceneKey = ''
        SceneMarkerX = $null
    }

    $spec_panel.Add_Paint({
        param($sender, $event_args)
        try {
            draw_spectrum_graph -graphics $event_args.Graphics -width $sender.ClientSize.Width -height $sender.ClientSize.Height
        }
        catch {
            write_runtime_log -message ('频谱绘制异常 {0}:{1}行: {2}' -f (Split-Path -Leaf $_.InvocationInfo.ScriptName), $_.InvocationInfo.ScriptLineNumber, $_.Exception.Message)
        }
    })
    $spec_panel.Add_MouseMove({
        param($sender, $event_args)
        if ($script:spectrum_state) {
            $script:spectrum_state.MarkerX = [double]$event_args.X
            if (-not $sender.IsDisposed) { $sender.Invalidate() }
        }
    })
    $spec_panel.Add_MouseLeave({
        if ($script:spectrum_state) {
            $script:spectrum_state.MarkerX = $null
            if ($script:spectrum_state.SpecPanel -and -not $script:spectrum_state.SpecPanel.IsDisposed) {
                $script:spectrum_state.SpecPanel.Invalidate()
            }
        }
    })
    $wf_panel.Add_Paint({
        param($sender, $event_args)
        try {
            draw_spectrum_waterfall -graphics $event_args.Graphics -width $sender.ClientSize.Width -height $sender.ClientSize.Height
        }
        catch {
            write_runtime_log -message ('瀑布绘制异常 {0}:{1}行: {2}' -f (Split-Path -Leaf $_.InvocationInfo.ScriptName), $_.InvocationInfo.ScriptLineNumber, $_.Exception.Message)
        }
    })

    $refresh_timer = New-Object System.Windows.Forms.Timer
    $refresh_timer.Interval = 350
    $refresh_timer.Add_Tick({
        try {
            if ($script:spectrum_state -and $script:spectrum_state.CheckAuto.Checked -and -not $script:spectrum_state.Frozen) {
                if ($script:spectrum_state.SpecPanel -and -not $script:spectrum_state.SpecPanel.IsDisposed) {
                    $script:spectrum_state.SpecPanel.Invalidate()
                }
                if ($script:spectrum_state.CheckWf.Checked -and $script:spectrum_state.WfPanel -and -not $script:spectrum_state.WfPanel.IsDisposed) {
                    $script:spectrum_state.WfPanel.Invalidate()
                }
            }
        }
        catch {
        }
    })
    $refresh_timer.Start()
    # 随内容根控件销毁清理（页签模式 = 波形窗口关闭；独立窗口模式 = 窗体关闭）
    $host_root.Add_Disposed({
        try {
            $refresh_timer.Stop()
            $refresh_timer.Dispose()
        }
        catch {
        }
        if ($script:spectrum_state -and $script:spectrum_state.SceneBmp) {
            try { $script:spectrum_state.SceneBmp.Dispose() } catch { }
        }
        $script:spectrum_state = $null
        $script:spectrum_form = $null
    })

    $invalidate = {
        if ($script:spectrum_state -and $script:spectrum_state.SpecPanel -and -not $script:spectrum_state.SpecPanel.IsDisposed) {
            $script:spectrum_state.SpecPanel.Invalidate()
        }
    }
    foreach ($ctrl in @($combo_ch, $combo_pts, $text_fs, $text_ref, $combo_div, $combo_rbw)) {
        $ctrl.Add_TextChanged($invalidate)
    }

    $button_clear_peak.Add_Click({
        if ($script:spectrum_state) {
            $script:spectrum_state.MaxHold = $null
            $script:spectrum_state.AvgDb = $null
        }
    })

    $check_log.Add_Click({
        if ($script:spectrum_state) {
            # 色标切换后已入库的旧行与新映射不一致，清空瀑布重新积累
            $script:spectrum_state.Waterfall.Clear()
            if ($script:spectrum_state.WfPanel -and -not $script:spectrum_state.WfPanel.IsDisposed) {
                $script:spectrum_state.WfPanel.Invalidate()
            }
        }
    })

    $button_freeze.Add_Click({
        if ($script:spectrum_state) {
            $script:spectrum_state.Frozen = -not $script:spectrum_state.Frozen
            $script:spectrum_state.ButtonFreeze.Text = $(if ($script:spectrum_state.Frozen) { '继续' } else { '冻结' })
            if ($script:spectrum_state.SpecPanel -and -not $script:spectrum_state.SpecPanel.IsDisposed) {
                $script:spectrum_state.SpecPanel.Invalidate()
            }
            if ($script:spectrum_state.WfPanel -and -not $script:spectrum_state.WfPanel.IsDisposed) {
                $script:spectrum_state.WfPanel.Invalidate()
            }
        }
    })

    # 两种模式都记到 spectrum_form（独立窗口模式此前漏赋值，重复打开会叠窗）
    $script:spectrum_form = $host_root
    if ($form) {
        [void]$form.Show($script:wave_form)
        try {
            [DwmDark]::EnableDarkTitleBar($form.Handle)
        }
        catch {
        }
    }
}

function get_spectrum_ref_scale {
    # 读参考电平 / dB每格（纯显示标度：y 轴顶=参考电平，共 10 格向下）
    # 只影响绘制与瀑布映射，不参与 FFT；解析失败回落 0 dB / 10 dB每格
    $ref = 0.0
    [void][double]::TryParse([string]$script:spectrum_state.TextRef.Text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$ref)
    $div = 10.0
    [void][double]::TryParse([string]$script:spectrum_state.ComboDiv.SelectedItem, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$div)
    if ($div -le 0 -or $div -gt 50) { $div = 10.0 }
    return @{ Ref = $ref; DivDb = $div; Bottom = ($ref - 10.0 * $div) }
}

function get_spectrum_analysis {
    # 拉数据 → FFT → dB；参数变化时重置峰值保持/平均/瀑布。
    # 返回 @{ Db; Used; Fs; Rbw; Key; Channel }，无数据返回 $null。
    # 频谱图与瀑布图共用本结果：同帧数据（frame_total+参数相同）直接命中缓存，
    # 避免 FFT 重复计算与瀑布插入重复行。
    $channel = [int]$script:spectrum_state.ComboCh.SelectedIndex
    $points = 0
    [void][int]::TryParse([string]$script:spectrum_state.ComboPts.SelectedItem, [ref]$points)
    if ($points -lt 64) { $points = 2048 }
    $fs = 1000.0
    [void][double]::TryParse($script:spectrum_state.TextFs.Text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$fs)
    if ($fs -le 0) { $fs = 1000.0 }

    # 手动 RBW：由 采样率/RBW 反推 FFT 长度（2 的幂，512..65536）
    # 注意 PS5.1 没有 [Math]::Log2（.NET Framework 无此方法），必须用 [Math]::Log($n, 2)
    $rbw_manual = 0.0
    [void][double]::TryParse([string]$script:spectrum_state.ComboRbw.SelectedItem, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$rbw_manual)
    if ($rbw_manual -gt 0) {
        $n = $fs / $rbw_manual
        if ($n -lt 1) { $n = 1.0 }
        $pow = [int][Math]::Round([Math]::Log($n, 2))
        $points = [Math]::Max(512, [Math]::Min(65536, [int][Math]::Pow(2, $pow)))
    }

    $key = ('{0}|{1}|{2}' -f $points, $fs, $channel)
    $data_ver = ('{0}|{1}' -f $script:wave_frame_total, $key)
    if ($script:spectrum_state.LastAnalysis -and $script:spectrum_state.LastAnalysis.Ver -eq $data_ver) {
        return $script:spectrum_state.LastAnalysis.Result
    }

    $samples = Get-WavePluginSnapshot -Channel $channel -Count $points
    # 兜底判空：宿主合同已保证返回 double[]，但插件侧仍守住 $null，
    # 避免 StrictMode 下 $samples.Count 抛「找不到属性 Count」刷屏。
    if ($null -eq $samples -or $samples.Count -lt 32) {
        return $null
    }

    $used = 0
    $mag = [WaveDsp]::MagnitudeSpectrum($samples, [ref]$used)
    if ($used -le 0) {
        return $null
    }

    $db = [WaveDsp]::ToDb($mag)

    # 参数变化 → 轨迹与瀑布重置
    if ($script:spectrum_state.LastSpectrumKey -ne $key) {
        $script:spectrum_state.MaxHold = $null
        $script:spectrum_state.AvgDb = $null
        $script:spectrum_state.Waterfall.Clear()
        $script:spectrum_state.LastSpectrumKey = $key
    }

    # 峰值保持 / 指数平均（批量计算下沉 C#，PS 逐 bin 循环是热路径瓶颈）
    if ($script:spectrum_state.CheckPeak.Checked) {
        if ($null -eq $script:spectrum_state.MaxHold -or $script:spectrum_state.MaxHold.Count -ne $db.Count) {
            $script:spectrum_state.MaxHold = [WaveDsp]::FillArray([double[]]::new($db.Count), -140.0)
        }
        [WaveDsp]::UpdateMaxHold($db, $script:spectrum_state.MaxHold)
    }
    if ($script:spectrum_state.CheckAvg.Checked) {
        if ($null -eq $script:spectrum_state.AvgDb -or $script:spectrum_state.AvgDb.Count -ne $db.Count) {
            $script:spectrum_state.AvgDb = [WaveDsp]::CopyArray($db)
        }
        else {
            [WaveDsp]::UpdateEma($db, $script:spectrum_state.AvgDb, 0.25)
        }
    }

    $result = @{
        Db = $db
        Used = $used
        Fs = $fs
        Df = ($fs / $used)                       # bin 间隔
        Rbw = (1.5 * $fs / $used)                # ENBW（汉宁窗等效噪声带宽）
        Key = $key
        Channel = $channel
    }
    $script:spectrum_state.LastAnalysis = @{ Ver = $data_ver; Result = $result }
    return $result
}

function draw_spectrum_graph {
    # 分层渲染：网格+轨迹+标注缓存为位图（数据/参数/尺寸变化才重画），
    # Marker 移动只做 位图 blit + 覆盖层（GDI+ AA 折线墨量大，每帧全重绘可达 50~170ms）
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height
    )
    if ($width -lt 80 -or $height -lt 80) { return }

    $st = $script:spectrum_state
    $analysis = get_spectrum_analysis
    $scale = get_spectrum_ref_scale
    $scene_key = '{0}|{1}x{2}|{3}|{4}|{5}|{6}|{7}|{8}' -f $script:wave_frame_total, $width, $height, $(if ($null -ne $analysis) { $analysis.Key } else { 'nodata' }), $scale.Ref, $scale.DivDb, $st.CheckPeak.Checked, $st.CheckAvg.Checked, $st.Frozen

    # 注意：System.Drawing.Bitmap 没有 IsDisposed 属性（那是控件的概念），判空即可
    $cache_ok = ($null -ne $st.SceneBmp) -and `
        ($st.SceneBmp.Width -eq $width) -and ($st.SceneBmp.Height -eq $height) -and ($st.SceneKey -eq $scene_key)
    if (-not $cache_ok) {
        if ($null -ne $st.SceneBmp) { $st.SceneBmp.Dispose() }
        $st.SceneBmp = New-Object System.Drawing.Bitmap($width, $height)
        $scene_g = [System.Drawing.Graphics]::FromImage($st.SceneBmp)
        try {
            render_spectrum_scene -graphics $scene_g -width $width -height $height -analysis $analysis -scale $scale
        }
        finally {
            $scene_g.Dispose()
        }
        $st.SceneKey = $scene_key
        $st.SceneMarkerX = $null
    }

    $graphics.DrawImage($st.SceneBmp, 0, 0, $width, $height)

    if ($null -ne $st.MarkerX) {
        draw_spectrum_marker -graphics $graphics -width $width -height $height -analysis $analysis -scale $scale -mx ([double]$st.MarkerX)
    }
}

function draw_spectrum_marker {
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height,
        $analysis,
        $scale,
        [double]$mx
    )
    if ($null -eq $analysis) { return }

    $plot_left = 56
    $plot_top = 10
    $plot_right = 14
    $plot_bottom = 26
    $plot_w = $width - $plot_left - $plot_right
    $plot_h = $height - $plot_top - $plot_bottom
    if ($plot_w -lt 60 -or $plot_h -lt 60) { return }

    $db = $analysis.Db
    $bins = $db.Count
    $fs = $analysis.Fs
    $db_top = $scale.Ref
    $db_bottom = $scale.Bottom
    $axis_font = new_font -size 8.25
    $text_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(215, 222, 235))

    if ($mx -ge $plot_left -and $mx -le ($plot_left + $plot_w)) {
        $bin_f = ($mx - $plot_left) / $plot_w * ($bins - 1)
        $bin_idx = [int][Math]::Round($bin_f)
        if ($bin_idx -lt 0) { $bin_idx = 0 }
        if ($bin_idx -gt ($bins - 1)) { $bin_idx = $bins - 1 }
        $m_freq = $bin_idx * $fs / $analysis.Used
        $m_db = $db[$bin_idx]
        $marker_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 150, 150))
        $marker_pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dot
        $graphics.DrawLine($marker_pen, [float]$mx, [float]$plot_top, [float]$mx, [float]($plot_top + $plot_h))
        $marker_pen.Dispose()
        $marker_text = ('{0:N2} Hz  {1:N1} dB' -f $m_freq, $m_db)
        $text_size = $graphics.MeasureString($marker_text, $axis_font)
        $tip_bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(200, 40, 30, 20))
        $tip_x = $mx + 8
        if ($tip_x + $text_size.Width -gt ($plot_left + $plot_w)) { $tip_x = $mx - 8 - $text_size.Width }
        $graphics.FillRectangle($tip_bg, [float]$tip_x, [float]($plot_top + 2), [float]($text_size.Width + 6), [float]$text_size.Height)
        $graphics.DrawString($marker_text, $axis_font, $text_brush, [float]($tip_x + 3), [float]($plot_top + 2))
        $tip_bg.Dispose()
    }

    $text_brush.Dispose()
    $axis_font.Dispose()
}

function render_spectrum_scene {
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height,
        $analysis,
        $scale
    )

    # 背景微渐变（与主波形窗口一致）
    $bg = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.Point(0, 0)),
        (New-Object System.Drawing.Point(0, $height)),
        $script:theme.PlotBgTop,
        $script:theme.PlotBgBottom)
    $graphics.FillRectangle($bg, 0, 0, $width, $height)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $plot_left = 56
    $plot_top = 10
    $plot_right = 14
    $plot_bottom = 26
    $plot_w = $width - $plot_left - $plot_right
    $plot_h = $height - $plot_top - $plot_bottom
    if ($plot_w -lt 60 -or $plot_h -lt 60) { $bg.Dispose(); return }

    # 绘图区描边框
    $frame_pen = New-Object System.Drawing.Pen($script:theme.PlotFrame)
    $graphics.DrawRectangle($frame_pen, [float]$plot_left, [float]$plot_top, [float]$plot_w, [float]$plot_h)
    $frame_pen.Dispose()

    $label_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(168, 178, 195))
    $text_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(215, 222, 235))
    $grid_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 55, 70))
    $axis_font = new_font -size 8.25

    if ($null -eq $analysis) {
        $hint = '等待数据…（连接串口 / 开启模拟数据 / FPGA 锯齿波）'
        $size = $graphics.MeasureString($hint, $axis_font)
        $graphics.DrawString($hint, $axis_font, $label_brush, [float](($width - $size.Width) / 2), [float](($height - $size.Height) / 2))
        if ($script:spectrum_state) { $script:spectrum_state.PeakLabel.Text = '主频: --' }
        $bg.Dispose(); $label_brush.Dispose(); $text_brush.Dispose(); $grid_pen.Dispose(); $axis_font.Dispose()
        return
    }

    $db = $analysis.Db
    $bins = $db.Count
    $fs = $analysis.Fs
    $f_max = $fs / 2.0
    $db_top = $scale.Ref
    $db_bottom = $scale.Bottom
    $div_db = $scale.DivDb
    $db_range = 10.0 * $div_db
    $y_scale = $plot_h / $db_range

    # 横向网格：10 格，每格 div_db dB，顶格即参考电平
    $grid_guard = 0
    $k = 0
    while ($k -le 10 -and $grid_guard -lt 12) {
        $yy = $plot_top + $k * ($plot_h / 10.0)
        $graphics.DrawLine($grid_pen, [float]$plot_left, [float]$yy, [float]($plot_left + $plot_w), [float]$yy)
        $db_line = $db_top - $k * $div_db
        $graphics.DrawString(('{0:N0}' -f $db_line), $axis_font, $label_brush, 4, [float]($yy - 7))
        $k++
        $grid_guard++
    }
    # 纵向网格：频率
    $x_step = get_nice_step -range $f_max -target_lines 8
    $grid_guard = 0
    $freq = 0.0
    while ($freq -le ($f_max + $x_step * 0.001) -and $grid_guard -lt 40) {
        $xx = $plot_left + ($freq / $f_max) * $plot_w
        $graphics.DrawLine($grid_pen, [float]$xx, [float]$plot_top, [float]$xx, [float]($plot_top + $plot_h))
        $label = if ($f_max -ge 10000) { '{0:N0}k' -f ($freq / 1000) } else { '{0:N0}' -f $freq }
        $graphics.DrawString($label, $axis_font, $label_brush, [float]($xx - 10), [float]($plot_top + $plot_h + 4))
        $freq += $x_step
        $grid_guard++
    }

    # 谱线绘制委托（当前/峰值/平均三轨迹共用）；屏幕点列在 C# 批量生成并按列 min/max 抽稀保峰
    $draw_trace = {
        param($data, [System.Drawing.Color]$color, [float]$line_width, [switch]$fill)
        $pen = New-Object System.Drawing.Pen($color)
        $pen.Width = $line_width
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        $pts = [WaveDsp]::SpectrumTracePointsDecimated($data, [int](2 * $plot_w), [float]$plot_left, [float]$plot_top, [float]$plot_w, [float]$plot_h, [float]$db_top, [float]$db_bottom)
        if ($fill -and $pts.Length -gt 1) {
            $fill_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(30, $color.R, $color.G, $color.B))
            $fill_poly = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
            $fill_poly.AddRange($pts)
            $fill_poly.Add([System.Drawing.PointF]::new([float]$pts[$pts.Length - 1].X, [float]($plot_top + $plot_h)))
            $fill_poly.Add([System.Drawing.PointF]::new([float]$pts[0].X, [float]($plot_top + $plot_h)))
            $graphics.FillPolygon($fill_brush, $fill_poly.ToArray())
            $fill_brush.Dispose()
        }
        $graphics.DrawLines($pen, $pts)
        $pen.Dispose()
    }

    if ($script:spectrum_state.CheckPeak.Checked -and $script:spectrum_state.MaxHold) {
        & $draw_trace $script:spectrum_state.MaxHold ([System.Drawing.Color]::FromArgb(160, 200, 90)) 1.2
    }
    if ($script:spectrum_state.CheckAvg.Checked -and $script:spectrum_state.AvgDb) {
        & $draw_trace $script:spectrum_state.AvgDb ([System.Drawing.Color]::FromArgb(80, 190, 200)) 1.2
    }
    & $draw_trace $db ([System.Drawing.Color]::FromArgb(120, 210, 255)) 1.8 -fill

    # 主频标注（跳过 DC；C# 批量搜索）
    $peak_bin = [WaveDsp]::MaxBin($db, 1)
    $peak_freq = $peak_bin * $fs / $analysis.Used
    $peak_db = $db[$peak_bin]
    $script:spectrum_state.PeakLabel.Text = ('主频: {0:N2} Hz @ {1:N1} dB' -f $peak_freq, $peak_db)
    $peak_x = $plot_left + ($peak_bin / ($bins - 1)) * $plot_w
    $peak_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 200, 90))
    $peak_pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
    $graphics.DrawLine($peak_pen, [float]$peak_x, [float]$plot_top, [float]$peak_x, [float]($plot_top + $plot_h))
    $peak_pen.Dispose()

    # Marker 覆盖层已移至 draw_spectrum_marker（分层渲染：鼠标移动不重画场景）

    $info = ('Δf {0:N3} Hz | RBW(ENBW) {1:N3} Hz | 采样率 {2:N0} Hz | {3} 点 FFT（汉宁窗）| 通道 {4}' -f $analysis.Df, $analysis.Rbw, $fs, $analysis.Used, (get_wave_channel_display_name -index $analysis.Channel))
    $graphics.DrawString($info, $axis_font, $text_brush, [float]($plot_left + 6), [float]($plot_top + $plot_h - 16))

    if ($script:spectrum_state.Frozen) {
        $frozen_font = new_font -size 9 -style Bold
        $frozen_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 120, 110))
        $graphics.DrawString('已冻结', $frozen_font, $frozen_brush, [float]($width - 76), [float]($plot_top + 4))
        $frozen_font.Dispose()
        $frozen_brush.Dispose()
    }

    $bg.Dispose()
    $label_brush.Dispose()
    $text_brush.Dispose()
    $grid_pen.Dispose()
    $axis_font.Dispose()
}

function draw_spectrum_waterfall {
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height
    )

    $bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(16, 19, 26))
    $graphics.FillRectangle($bg, 0, 0, $width, $height)

    if (-not $script:spectrum_state -or -not $script:spectrum_state.CheckWf.Checked) {
        $bg.Dispose()
        return
    }

    $analysis = get_spectrum_analysis
    if ($null -eq $analysis) {
        $bg.Dispose()
        return
    }

    # 新谱帧入瀑布（最新在顶）；与频谱图共用同一分析结果（同帧数据）
    # 颜色映射跟随参考电平标度（顶=参考电平，底=参考电平-10×dB每格）；
    # 对数色标开启时 dB→颜色前先 t^0.5（gamma<1），拉开低电平层次
    $scale = get_spectrum_ref_scale
    $gamma = 1.0
    if ($script:spectrum_state.CheckLog.Checked) { $gamma = 0.5 }
    $wf = $script:spectrum_state.Waterfall
    $row_bytes = [WaveDsp]::WaterfallRow($analysis.Db, $scale.Bottom, $scale.Ref, $gamma)
    if ($script:spectrum_state.WfBins -ne $row_bytes.Length) {
        $wf.Clear()
        $script:spectrum_state.WfBins = $row_bytes.Length
    }
    $wf.Insert(0, $row_bytes)
    while ($wf.Count -gt 120) {
        $wf.RemoveAt($wf.Count - 1)
    }

    # 渲染：行缓冲拼成整块 → LockBits 一次写入（避免逐像素 SetPixel）
    $rows = $wf.Count
    $w = $script:spectrum_state.WfBins / 3
    if ($rows -lt 2 -or $w -lt 4) { $bg.Dispose(); return }

    $bitmap = New-Object System.Drawing.Bitmap($w, $rows, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $rows)
    $bitmap_data = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    try {
        $stride = $bitmap_data.Stride
        $buffer = [byte[]]::new($stride * $rows)
        for ($r = 0; $r -lt $rows; $r++) {
            [Array]::Copy($wf[$r], 0, $buffer, $r * $stride, $row_bytes.Length)
        }
        [System.Runtime.InteropServices.Marshal]::Copy($buffer, 0, $bitmap_data.Scan0, $buffer.Length)
    }
    finally {
        $bitmap.UnlockBits($bitmap_data)
    }

    $plot_left = 56
    $plot_right = 14
    $plot_w = $width - $plot_left - $plot_right
    $plot_h = $height - 6
    $dest_rect = New-Object System.Drawing.Rectangle($plot_left, 3, $plot_w, $plot_h)
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $graphics.DrawImage($bitmap, $dest_rect)
    $bitmap.Dispose()

    $axis_font = new_font -size 8.25
    $label_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(168, 178, 195))
    $f_max = $analysis.Fs / 2.0
    $graphics.DrawString(('0 Hz'), $axis_font, $label_brush, 4, 3)
    $graphics.DrawString(('{0:N0} Hz' -f $f_max), $axis_font, $label_brush, 4, [float]($plot_h - 10))
    $graphics.DrawString(('瀑布（最新在顶，{0:N0} ~ {1:N0} dB）  行数 {2}' -f $scale.Bottom, $scale.Ref, $rows), $axis_font, $label_brush, [float]($plot_left + 6), 3)

    $axis_font.Dispose()
    $label_brush.Dispose()
    $bg.Dispose()
}

Register-WavePlugin -Name '频谱' -Description '频谱仪：FFT dB 谱 + 峰值保持/平均 + RBW + 瀑布图 + Marker' -OpenPanel ${function:show_spectrum_panel}
