# Spectrum plugin regression test (headless, no user data touched).
# Run: powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File test_spectrum_plugin.ps1
# Prints SPECTRUMTEST_OK and exits 0 on success; exits 1 with diagnostics on failure.
# Covers: plugin load/register, panel open, analysis pipeline (FFT/dB/cache),
# param-change reset, draw + waterfall paths. Built after the "-graphics vs $g"
# param mismatch that blanked the panel with swallowed paint exceptions.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$failures = New-Object 'System.Collections.Generic.List[string]'
$script:log_lines = New-Object 'System.Collections.Generic.List[string]'

function assert-true([bool]$condition, [string]$what) {
    if ($condition) { Write-Output ('  PASS  ' + $what) }
    else { Write-Output ('  FAIL  ' + $what); $failures.Add($what) }
}

$app_src = Get-Content -Raw -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath 'serial_terminal_v6.ps1')

# --- extract WaveDsp C# from host and compile ---
$m = [regex]::Match($app_src, "(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@")
if (-not $m.Success) { Write-Output 'FATAL: WaveDsp here-string not found'; exit 1 }
Add-Type -TypeDefinition $m.Groups[1].Value -ReferencedAssemblies @('System.Drawing')

# --- extract real host functions ---
function extract-func([string]$text, [string]$name) {
    $fm = [regex]::Match($text, "(?m)^function\s+([\-0-9A-Za-z]+:)?$([regex]::Escape($name))\s*\{")
    if (-not $fm.Success) { throw "function not found: $name" }
    $start = $text.IndexOf('{', $fm.Index)
    $depth = 0
    for ($j = $start; $j -lt $text.Length; $j++) {
        $c = $text[$j]
        if ($c -eq '{') { $depth++ }
        elseif ($c -eq '}') { $depth--; if ($depth -eq 0) { return $text.Substring($fm.Index, $j - $fm.Index + 1) } }
    }
    throw "unbalanced braces: $name"
}
Invoke-Expression (extract-func $app_src 'new_font')
Invoke-Expression (extract-func $app_src 'get_nice_step')
Invoke-Expression (extract-func $app_src 'Get-WavePluginSnapshot')
Invoke-Expression (extract-func $app_src 'Register-WavePlugin')

# --- host env stubs ---
function style_wave_button { param($button) }
function get_wave_channel_display_name { param([int]$index) return ('CH{0}' -f $index) }
function write_runtime_log { param([string]$message) $script:log_lines.Add($message) }

# theme table: the real one lives in host top-level (harness does not run host top-level)
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

$script:wave_plugins = New-Object 'System.Collections.ArrayList'
$script:wave_ch_values = New-Object 'System.Collections.Generic.List[object]'
for ($ch = 0; $ch -lt 8; $ch++) {
    $lst = New-Object 'System.Collections.Generic.List[double]'
    for ($i = 0; $i -lt 1491; $i++) { [void]$lst.Add([double]($i % 256)) }   # sawtooth, odd length on purpose
    [void]$script:wave_ch_values.Add($lst)
}
$script:wave_frame_total = [long]1491

# --- load plugin exactly like the host (top-level dot-source) ---
. (Join-Path -Path $PSScriptRoot -ChildPath 'plugins\spectrum\plugin.ps1')
assert-true ($script:wave_plugins.Count -eq 1 -and $script:wave_plugins[0].Name -eq '频谱') 'plugin registered as 频谱'

$owner = New-Object System.Windows.Forms.Form
$owner.Show()
$script:wave_form = $owner
& $script:wave_plugins[0].OpenPanel
[System.Windows.Forms.Application]::DoEvents()
Start-Sleep -Milliseconds 150
[System.Windows.Forms.Application]::DoEvents()

$st = $script:spectrum_state
assert-true ($null -ne $st) 'spectrum state created'
if ($null -eq $st) { Write-Output 'SPECTRUMTEST_FAILED'; exit 1 }
assert-true ($null -ne $st.LastAnalysis) 'state preset includes LastAnalysis (StrictMode safe)'

Write-Output '=== analysis pipeline ==='
try {
    $a1 = get_spectrum_analysis
    assert-true ($null -ne $a1) 'analysis returns result for 1491 samples'
    if ($null -ne $a1) {
        assert-true ($a1.Used -eq 1024) ('FFT used=1024 (largest pow2 <= 1491), got ' + $a1.Used)
        assert-true ($a1.Db.Count -eq ($a1.Used / 2)) ('bins = used/2 = ' + $a1.Db.Count)
        $ver1 = $st.LastAnalysis.Ver
        $a1b = get_spectrum_analysis
        assert-true ([object]::ReferenceEquals($a1, $a1b)) 'same-frame second call hits cache'
        assert-true ($null -ne $st.MaxHold) 'peak hold built'
        assert-true ($null -ne $st.AvgDb) 'average trace built'
    }
}
catch {
    Write-Output ('  ANALYSIS EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('analysis pipeline threw')
}

Write-Output '=== param change resets traces ==='
try {
    $old_key = $st.LastSpectrumKey
    $st.ComboPts.SelectedIndex = 1   # 1024
    $a2 = get_spectrum_analysis
    assert-true ($null -ne $a2) 'analysis after param change'
    assert-true ($st.LastSpectrumKey -ne $old_key) 'LastSpectrumKey changed'
    assert-true ($null -ne $st.MaxHold -and $st.MaxHold.Count -eq 512) 'peak hold rebuilt for 512 bins'
}
catch {
    Write-Output ('  PARAM-CHANGE EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('param-change path threw')
}

Write-Output '=== ref level / scale settings ==='
try {
    $sc = get_spectrum_ref_scale
    assert-true ($sc.Ref -eq 0.0 -and $sc.DivDb -eq 10.0 -and $sc.Bottom -eq -100.0) ('defaults Ref=0 Div=10 Bottom=-100, got ' + $sc.Ref + '/' + $sc.DivDb + '/' + $sc.Bottom)
    $st.TextRef.Text = '-10'
    $st.ComboDiv.SelectedIndex = 2   # 20
    $sc2 = get_spectrum_ref_scale
    assert-true ($sc2.Ref -eq -10.0 -and $sc2.DivDb -eq 20.0 -and $sc2.Bottom -eq -210.0) ('Ref=-10 Div=20 Bottom=-210, got ' + $sc2.Ref + '/' + $sc2.DivDb + '/' + $sc2.Bottom)
    $st.TextRef.Text = 'abc'         # 非法输入回落
    $sc3 = get_spectrum_ref_scale
    assert-true ($sc3.Ref -eq 0.0) 'invalid ref text falls back to 0'
    $st.TextRef.Text = '-10'
}
catch {
    Write-Output ('  SCALE EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('ref-scale path threw')
}

Write-Output '=== manual RBW derives FFT length ==='
try {
    $idx_rbw = -1
    for ($i = 0; $i -lt $st.ComboRbw.Items.Count; $i++) { if ($st.ComboRbw.Items[$i] -eq '1') { $idx_rbw = $i; break } }
    assert-true ($idx_rbw -ge 0) 'RBW combo contains 1 Hz entry'
    $st.ComboRbw.SelectedIndex = $idx_rbw   # Fs=1000 → N=2^round(log2(1000))=1024
    $a3 = get_spectrum_analysis
    assert-true ($null -ne $a3) 'analysis with manual RBW'
    if ($null -ne $a3) {
        assert-true ($a3.Used -eq 1024) ('manual RBW 1Hz @ Fs=1000 -> used=1024, got ' + $a3.Used)
        assert-true ([Math]::Abs($a3.Rbw - (1.5 * 1000.0 / 1024.0)) -lt 0.001) ('ENBW = 1.5*Fs/used, got ' + $a3.Rbw)
        assert-true ([Math]::Abs($a3.Df - (1000.0 / 1024.0)) -lt 0.001) 'bin spacing = Fs/used'
    }
    $st.ComboRbw.SelectedIndex = 0          # back to auto
}
catch {
    Write-Output ('  RBW EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('manual RBW path threw')
}

Write-Output '=== waterfall log color scale (gamma) ==='
try {
    $db_one = [double[]]@(-90.0)   # 低电平：0~-100 窗内 t=0.1
    $row_lin = [WaveDsp]::WaterfallRow($db_one, -100.0, 0.0, 1.0)
    $row_log = [WaveDsp]::WaterfallRow($db_one, -100.0, 0.0, 0.5)
    $sum_lin = ($row_lin | Measure-Object -Sum).Sum
    $sum_log = ($row_log | Measure-Object -Sum).Sum
    assert-true ($sum_log -gt $sum_lin) ('gamma 0.5 brightens -90dB (lin=' + $sum_lin + ' < log=' + $sum_log + ')')
    $row_top = [WaveDsp]::WaterfallRow([double[]]@(-100.0), -100.0, 0.0, 0.5)
    assert-true (($row_top | Measure-Object -Sum).Sum -gt 0) 'gamma keeps bottom-of-range visible (t^0.5 of 0 clamped > 0 not required; row rendered)'
    $st.CheckLog.Checked = $false
    $st.CheckLog.Checked = $true   # 触发清空瀑布的切换处理路径
}
catch {
    Write-Output ('  GAMMA EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('waterfall gamma path threw')
}

Write-Output '=== draw paths (call-site param names) ==='
try {
    $bmp = New-Object System.Drawing.Bitmap(900, 320)
    $g2 = [System.Drawing.Graphics]::FromImage($bmp)
    draw_spectrum_graph -graphics $g2 -width 900 -height 320
    assert-true ($null -ne $st.SceneBmp) 'scene cache bitmap created'
    $key1 = $st.SceneKey
    draw_spectrum_graph -graphics $g2 -width 900 -height 320      # 同参数 → 命中缓存
    assert-true ($st.SceneKey -eq $key1) 'scene cache hit on identical draw'
    $st.MarkerX = 300.0
    draw_spectrum_graph -graphics $g2 -width 900 -height 320      # Marker 覆盖层路径
    assert-true $true 'marker overlay path OK'
    $st.MarkerX = $null
    $g2.Dispose(); $bmp.Dispose()
    assert-true $true 'draw_spectrum_graph -graphics OK'
}
catch {
    Write-Output ('  DRAW EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('draw_spectrum_graph threw')
}
try {
    $bmp3 = New-Object System.Drawing.Bitmap(900, 200)
    $g3 = [System.Drawing.Graphics]::FromImage($bmp3)
    draw_spectrum_waterfall -graphics $g3 -width 900 -height 200
    $g3.Dispose(); $bmp3.Dispose()
    assert-true $true 'draw_spectrum_waterfall -graphics OK'
}
catch {
    Write-Output ('  WATERFALL EXCEPTION: ' + $_.Exception.Message)
    Write-Output $_.InvocationInfo.PositionMessage
    $failures.Add('draw_spectrum_waterfall threw')
}

Write-Output '=== natural paint via message pump ==='
$st.SpecPanel.Invalidate()
$st.WfPanel.Invalidate()
[System.Windows.Forms.Application]::DoEvents()
Start-Sleep -Milliseconds 150
[System.Windows.Forms.Application]::DoEvents()
$bad = @($script:log_lines | Where-Object { $_ -match '异常' })
assert-true ($bad.Count -eq 0) ('no paint exceptions logged (got ' + $bad.Count + ')')
if ($bad.Count -gt 0) { $bad | ForEach-Object { Write-Output ('  LOG: ' + $_) } }

# Visual self-check screenshot (icons/fills/layout review) - BEFORE closing forms
try {
    $preview_path = Join-Path -Path $PSScriptRoot -ChildPath 'spectrum_preview.png'
    $bmp_prev = New-Object System.Drawing.Bitmap($st.Form.Width, $st.Form.Height)
    $st.Form.DrawToBitmap($bmp_prev, (New-Object System.Drawing.Rectangle(0, 0, $bmp_prev.Width, $bmp_prev.Height)))
    $bmp_prev.Save($preview_path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp_prev.Dispose()
    Write-Output ('preview: {0}' -f $preview_path)
}
catch {
    Write-Output ('preview exception: ' + $_.Exception.Message)
}

$owner.Close()
[System.Windows.Forms.Application]::DoEvents()

if ($failures.Count -gt 0) {
    Write-Output ('SPECTRUMTEST_FAILED (' + $failures.Count + ' failures)')
    exit 1
}
Write-Output 'SPECTRUMTEST_OK'
exit 0
