# run_stability_tests.ps1 —— 串口助手 V6 稳定性测试套件
# 用法（pwsh 或 powershell 均可）:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_stability_tests.ps1 [-GuiRunMinutes 10] [-SkipGuiLongRun]
# 测试 A: 大流量回放（22 万帧 > 20 万缓冲上限，验证 trim 与行数对账）
# 测试 B: 畸形数据 fuzz（超长行/NaN/空行/多通道/垃圾字节，验证不崩溃）
# 测试 C: GUI 长跑（控制通道远程开模拟数据，采样内存/UI跳过/帧数，TCP 反复连接）

param(
    [int]$GuiRunMinutes = 10,
    [switch]$SkipGuiLongRun
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$script_path = Join-Path $PSScriptRoot 'serial_terminal_v6.ps1'
$work_dir = Join-Path $env:TEMP ('stab_{0}' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $work_dir | Out-Null
$results = @()

function Add-Result {
    param([string]$Name, [bool]$Pass, [string]$Detail)
    $script:results += [pscustomobject]@{ Name = $Name; Pass = $Pass; Detail = $Detail }
    Write-Output ('{0} [{1}] {2}' -f $(if ($Pass) { 'PASS' } else { 'FAIL' }), $Name, $Detail)
}

Write-Output ('工作目录: ' + $work_dir)
Write-Output ('测试 A: 大流量回放（22 万帧，触发 20 万帧缓冲裁剪）')
Write-Output ('--------------------------------------------------')

# ---------- 测试 A: 大流量回放 ----------
$replay_a = Join-Path $work_dir 'heavy.jsonl'
$writer = [System.IO.StreamWriter]::new($replay_a, $false, [System.Text.UTF8Encoding]::new($false))
$total_frames_a = 0
$chunk_frames = 1000
for ($block = 0; $block -lt 220; $block++) {
    $lines_text = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $chunk_frames; $i++) {
        $v0 = [Math]::Sin(($total_frames_a + $i) / 37.0)
        $v1 = [Math]::Cos(($total_frames_a + $i) / 91.0)
        $v2 = ($total_frames_a + $i) % 1000 / 100.0
        [void]$lines_text.AppendLine(('{0:F5},{1:F5},{2:F5}' -f $v0, $v1, $v2))
    }
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lines_text.ToString()))
    $writer.WriteLine((ConvertTo-Json @{ t = 'x'; ms = 0; dir = 'DATA'; b64 = $b64; n = $chunk_frames } -Compress))
    $total_frames_a += $chunk_frames
}
$writer.Dispose()
Write-Output ('回放文件: {0} 帧 / {1:N1} MB' -f $total_frames_a, ((Get-Item $replay_a).Length / 1MB))

$out_a = Join-Path $work_dir 'heavy_out.jsonl'
$sw_a = [System.Diagnostics.Stopwatch]::StartNew()
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script_path -Headless -ReplayFile $replay_a -Out $out_a -Protocol FireWater 2>&1 | Out-Null
$exit_a = $LASTEXITCODE
$sw_a.Stop()

$data_lines_a = 0
$summary_a = $null
foreach ($line in [System.IO.File]::ReadLines($out_a)) {
    if ($line.StartsWith('{"dir":"DATA"') -or $line.StartsWith('{ "dir": "DATA"') -or $line -match '"dir"\s*:\s*"DATA"') { $data_lines_a++ }
    if ($line -match '"dir"\s*:\s*"SUMMARY"') { $summary_a = $line }
}
Add-Result -Name 'A.exit_code' -Pass ($exit_a -eq 0) -Detail ('退出码 {0}，耗时 {1:N1}s' -f $exit_a, $sw_a.Elapsed.TotalSeconds)
Add-Result -Name 'A.frames_total' -Pass ($summary_a -match '"frames":220000') -Detail ('SUMMARY: ' + $(if ($summary_a) { $summary_a.Substring(0, [Math]::Min(220, $summary_a.Length)) } else { '缺失' }))
Add-Result -Name 'A.buffered_cap' -Pass ($summary_a -match '"buffered":200000') -Detail ('缓冲裁剪后帧数应为 200000（20 万上限）')

Write-Output ''
Write-Output ('测试 B: 畸形数据 fuzz')
Write-Output ('--------------------------------------------------')

# ---------- 测试 B: 畸形数据 fuzz ----------
$replay_b = Join-Path $work_dir 'fuzz.jsonl'
$writer_b = [System.IO.StreamWriter]::new($replay_b, $false, [System.Text.UTF8Encoding]::new($false))
$fuzz_payloads = @()
$fuzz_payloads += ('')                                   # 空块
$fuzz_payloads += ("`n`n`n")                             # 纯换行
$fuzz_payloads += (',' * 500 + "`n")                     # 空字段超长
$fuzz_payloads += ('nan,nan,inf,-inf' + "`n")            # 非数值
$fuzz_payloads += ((1..200 | ForEach-Object { '1.5' }) -join ',' + "`n")   # 200 通道
$fuzz_payloads += (('x' * 65536) + "`n")                 # 64KB 超长行
$fuzz_payloads += ('1.23,4.56' + "`n")                   # 正常行（夹心）
$fuzz_payloads += ([System.Text.Encoding]::UTF8.GetString((1..4096 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 })) + "`n")  # 垃圾字符
$fuzz_payloads += ('1e999, -1e999' + "`n")               # 溢出科学计数法
for ($i = 0; $i -lt 500; $i++) {
    $payload = $fuzz_payloads[$i % $fuzz_payloads.Count]
    $b64_b = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($payload))
    $writer_b.WriteLine((ConvertTo-Json @{ t = 'x'; ms = 0; dir = 'DATA'; b64 = $b64_b; n = 1 } -Compress))
}
$writer_b.Dispose()

$out_b = Join-Path $work_dir 'fuzz_out.jsonl'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script_path -Headless -ReplayFile $replay_b -Out $out_b -Protocol FireWater 2>&1 | Out-Null
$exit_b = $LASTEXITCODE
$runtime_errors_b = 0
if (Test-Path (Join-Path $PSScriptRoot 'logs\runtime_error.log')) {
    $log_before = (Get-Item (Join-Path $PSScriptRoot 'logs\runtime_error.log')).Length
}
Add-Result -Name 'B.exit_code' -Pass ($exit_b -eq 0) -Detail ('畸形输入 500 块，退出码 {0}' -f $exit_b)

Write-Output ''
if ($SkipGuiLongRun) {
    Write-Output ('测试 C: 跳过（-SkipGuiLongRun）')
}
else {
    Write-Output ('测试 C: GUI 长跑 {0} 分钟（模拟数据 + 控制通道 + 内存采样）' -f $GuiRunMinutes)
    Write-Output ('--------------------------------------------------')

    # ---------- 测试 C: GUI 长跑 ----------
    $control_port = 9201
    $gui_proc = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', $script_path, '-ControlPort', [string]$control_port
    ) -PassThru -WindowStyle Minimized
    Write-Output ('GUI 子进程 PID: {0}' -f $gui_proc.Id)

    function Send-ControlCmd {
        param([object]$cmd_obj, [int]$timeout_ms = 6000)
        try {
            $tcp = [System.Net.Sockets.TcpClient]::new('127.0.0.1', $control_port)
            $tcp.ReceiveTimeout = $timeout_ms
            $stream = $tcp.GetStream()
            $req_bytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $cmd_obj -Compress -Depth 6) + "`n")
            $stream.Write($req_bytes, 0, $req_bytes.Length)
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $resp_text = $reader.ReadLine()
            $tcp.Close()
            if ($resp_text) {
                return $resp_text | ConvertFrom-Json
            }
        }
        catch {
            return $null
        }
        return $null
    }

    # 等待控制端口就绪
    $ready = $false
    $wait_deadline = [datetime]::Now.AddSeconds(30)
    while ([datetime]::Now -lt $wait_deadline) {
        $probe = Send-ControlCmd -cmd_obj @{ cmd = 'ping' }
        if ($probe -and $probe.ok) { $ready = $true; break }
        Start-Sleep -Milliseconds 800
    }
    Add-Result -Name 'C.gui_start' -Pass $ready -Detail ('控制端口 {0} 就绪（30s 内）' -f $control_port)

    if ($ready) {
        [void](Send-ControlCmd -cmd_obj @{ cmd = 'set_protocol'; protocol = 'FireWater' })
        # UI 繁忙时响应可能慢于客户端超时，用 status 二次确认实际状态
        [void](Send-ControlCmd -cmd_obj @{ cmd = 'wave_sim'; on = $true })
        Start-Sleep -Seconds 2
        $sim_check = Send-ControlCmd -cmd_obj @{ cmd = 'status' }
        Add-Result -Name 'C.sim_start' -Pass ($sim_check -and $sim_check.ok -and $sim_check.sim_on) -Detail ('远程开启模拟数据: sim_on=' + $(if ($sim_check) { $sim_check.sim_on } else { '无响应' }))

        # 采样循环
        $samples = New-Object System.Collections.Generic.List[object]
        $sample_interval = [TimeSpan]::FromSeconds(30)
        $run_duration = [TimeSpan]::FromMinutes($GuiRunMinutes)
        $next_sample = [datetime]::Now.AddSeconds(5)
        $end_at = [datetime]::Now.Add($run_duration)
        $tcp_churn_ok = $true
        $next_churn = [datetime]::Now.AddSeconds(20)

        while ([datetime]::Now -lt $end_at) {
            Start-Sleep -Milliseconds 500

            # TCP 反复连接断开（空连接压力）
            if ([datetime]::Now -ge $next_churn) {
                $next_churn = [datetime]::Now.AddSeconds(10)
                try {
                    $churn = [System.Net.Sockets.TcpClient]::new('127.0.0.1', $control_port)
                    $churn.Client.Close()
                    $churn.Close()
                }
                catch {
                    $tcp_churn_ok = $false
                }
            }

            if ([datetime]::Now -ge $next_sample) {
                $next_sample = [datetime]::Now.Add($sample_interval)
                $proc = Get-Process -Id $gui_proc.Id -ErrorAction SilentlyContinue
                $st = Send-ControlCmd -cmd_obj @{ cmd = 'status' }
                if ($proc -and $st -and $st.ok) {
                    $samples.Add([pscustomobject]@{
                        T = [int]((Get-Date) - [datetime]::Now.Date).TotalSeconds
                        Min = [int](($end_at - [datetime]::Now).TotalMinutes)
                        WS_MB = [Math]::Round($proc.WorkingSet64 / 1MB, 1)
                        Private_MB = [Math]::Round($proc.PrivateMemorySize64 / 1MB, 1)
                        Frames = $st.frame_count
                        Total = $st.frame_total
                        UiSkip = $st.ui_skip
                        RxEntries = $st.rx_entries
                        Sim = $st.sim_on
                    })
                    $s = $samples[-1]
                    Write-Output ('  [剩{0,2}min] 内存 {1} MB | 私有 {2} MB | 帧 {3}/{4} | UI跳过 {5} | RX条目 {6} | sim {7}' -f $s.Min, $s.WS_MB, $s.Private_MB, $s.Frames, $s.Total, $s.UiSkip, $s.RxEntries, $s.Sim)
                }
            }
        }

        $sim_stop = Send-ControlCmd -cmd_obj @{ cmd = 'wave_sim'; on = $false }
        Start-Sleep -Seconds 2
        $sim_stop_check = Send-ControlCmd -cmd_obj @{ cmd = 'status' }
        Add-Result -Name 'C.sim_stop' -Pass ($sim_stop_check -and $sim_stop_check.ok -and -not $sim_stop_check.sim_on) -Detail ('远程停止模拟数据: sim_on=' + $(if ($sim_stop_check) { $sim_stop_check.sim_on } else { '无响应' }))

        # 分析内存趋势：前 25% 均值 vs 后 25% 均值
        if ($samples.Count -ge 4) {
            $quarter = [Math]::Max(1, [int]($samples.Count / 4))
            $early = ($samples | Select-Object -First $quarter | Measure-Object -Property Private_MB -Average).Average
            $late = ($samples | Select-Object -Last $quarter | Measure-Object -Property Private_MB -Average).Average
            $growth = $late - $early
            $frames_total_c = $samples[-1].Total
            $ui_skip_c = $samples[-1].UiSkip
            Add-Result -Name 'C.memory' -Pass ($growth -lt 60) -Detail ('私有内存 {0:N1} → {1:N1} MB（增长 {2:N1} MB，阈值 60）' -f $early, $late, $growth)
            Add-Result -Name 'C.frames' -Pass ($frames_total_c -gt 1000) -Detail ('累计帧 {0}（模拟 {1} 分钟）' -f $frames_total_c, $GuiRunMinutes)
            Add-Result -Name 'C.ui_skip' -Pass ($ui_skip_c -eq 0) -Detail ('UI 跳过计数 {0}' -f $ui_skip_c)
        }
        Add-Result -Name 'C.tcp_churn' -Pass $tcp_churn_ok -Detail '测试期间空连接反复建断'

        # 进程仍存活？
        $still_alive = ($null -ne (Get-Process -Id $gui_proc.Id -ErrorAction SilentlyContinue))
        Add-Result -Name 'C.gui_alive' -Pass $still_alive -Detail '长跑后 GUI 进程存活'

        try {
            Stop-Process -Id $gui_proc.Id -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
}

Write-Output ''
Write-Output '=================================================='
$pass_count = ($results | Where-Object { $_.Pass }).Count
$fail_count = $results.Count - $pass_count
Write-Output ('稳定性测试汇总: {0} 通过 / {1} 失败' -f $pass_count, $fail_count)
foreach ($r in $results) {
    if (-not $r.Pass) {
        Write-Output ('  FAIL: {0} — {1}' -f $r.Name, $r.Detail)
    }
}
Write-Output ('工作目录保留: ' + $work_dir)
if ($fail_count -gt 0) { exit 1 }
exit 0
