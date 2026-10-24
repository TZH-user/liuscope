# core\control_channel.ps1 — AI 控制通道（TCP JSON 行协议）
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function handle_control_request {
    param([string]$request_line)

    try {
        $req = $request_line | ConvertFrom-Json
    }
    catch {
        return @{ ok = $false; error = ('JSON 解析失败: ' + $_.Exception.Message) }
    }

    $cmd = [string](get_json_prop $req 'cmd')
    switch ($cmd) {
        'ping' {
            return @{ ok = $true; tool = 'RK3562 串口收发工具 V5'; control = 'on'; docs = 'AI_INTERFACE.md' }
        }
        'help' {
            return @{
                ok = $true
                tool = 'RK3562 串口收发工具 V5'
                docs = 'AI_INTERFACE.md'
                commands = @{
                    ping = '存活探测'
                    status = '连接/协议/帧数/录制状态'
                    list_ports = '可用串口列表'
                    send = '发送数据: text/hex/newline'
                    read_lines = '读最近记录: count/filter(正则)'
                    wave_snapshot = '最近波形: count'
                    set_protocol = '切换协议: FireWater/JustFloat/RawData'
                    wave_clear = '清空波形缓冲'
                    wave_sim = '开关模拟数据源: on(true/false)'
                    record = '会话录制: on'
                    expect = '期望测试: steps[]'
                    set_hit_pattern = '设置RX命中高亮正则: pattern'
                    reset = '复位板卡: mode=rts|dtr|command(+command)'
                    flash = '执行烧录命令: command(+timeout_ms)，成功自动重连串口'
                }
            }
        }
        'set_hit_pattern' {
            set_hit_pattern -pattern ([string](get_json_prop $req 'pattern' ''))
            return @{ ok = $true; pattern = $script:hit_pattern_text }
        }
        'reset' {
            $mode = [string](get_json_prop $req 'mode' 'rts')
            try {
                if ($mode -eq 'command') {
                    $command = [string](get_json_prop $req 'command')
                    if ([string]::IsNullOrWhiteSpace($command)) {
                        return @{ ok = $false; error = 'command 模式需要 command 参数' }
                    }
                    $was_connected = $script:connected
                    if ($script:connected) {
                        close_serial_port
                    }
                    $result = invoke_stream_command -command $command -timeout_ms 120000
                    if ($was_connected) {
                        try {
                            connect_serial_port
                        }
                        catch {
                        }
                    }
                    return @{ ok = $true; mode = 'command'; exit_code = $result.exit_code; timed_out = $result.timed_out; lines = @($result.lines | Select-Object -Last 20) }
                }
                elseif ($mode -eq 'dtr') {
                    invoke_reset_pulse -mode 'dtr'
                    return @{ ok = $true; mode = 'dtr' }
                }
                elseif ($mode -eq 'rts') {
                    invoke_reset_pulse -mode 'rts'
                    return @{ ok = $true; mode = 'rts' }
                }
                else {
                    return @{ ok = $false; error = 'mode 必须是 rts/dtr/command' }
                }
            }
            catch {
                return @{ ok = $false; error = $_.Exception.Message }
            }
        }
        'flash' {
            $command = [string](get_json_prop $req 'command')
            if ([string]::IsNullOrWhiteSpace($command)) {
                return @{ ok = $false; error = '缺少 command' }
            }
            $timeout_ms = [int](get_json_prop $req 'timeout_ms' 300000)
            try {
                $was_connected = $script:connected
                if ($script:connected) {
                    close_serial_port
                }
                $result = invoke_stream_command -command $command -timeout_ms $timeout_ms
                $flash_success = (($result.exit_code -eq 0) -and (-not $result.timed_out))
                if ($was_connected) {
                    try {
                        connect_serial_port
                    }
                    catch {
                    }
                }
                return @{ ok = $true; success = $flash_success; exit_code = $result.exit_code; timed_out = $result.timed_out; lines = @($result.lines | Select-Object -Last 30) }
            }
            catch {
                return @{ ok = $false; error = $_.Exception.Message }
            }
        }
        'list_ports' {
            return @{ ok = $true; ports = @([System.IO.Ports.SerialPort]::GetPortNames()) }
        }
        'status' {
            return @{
                ok = $true
                connected = $script:connected
                port = $script:connected_port_name
                baud = $script:connected_baud_rate
                protocol = $script:wave_protocol
                frame_total = $script:wave_frame_total
                frame_count = $script:wave_frame_count
                channels = $script:wave_ch_values.Count
                rx_entries = $script:message_entries.Count
                ui_skip = $script:ui_skip_total
                pending_display = $script:pending_display_entries.Count
                sim_on = $script:wave_sim_on
                recording = ($null -ne $script:session_record_writer)
                hit_pattern = $script:hit_pattern_text
            }
        }
        'send' {
            try {
                send_control_payload -text ([string](get_json_prop $req 'text')) -as_hex ([bool](get_json_prop $req 'hex' $false)) -newline_mode ([string](get_json_prop $req 'newline' 'CRLF'))
                return @{ ok = $true }
            }
            catch {
                return @{ ok = $false; error = $_.Exception.Message }
            }
        }
        'read_lines' {
            $count = [Math]::Max(1, [int](get_json_prop $req 'count' 50))
            $filter = [string](get_json_prop $req 'filter' '')
            $start = [Math]::Max(0, $script:message_entries.Count - $count)
            $lines = @()
            for ($i = $start; $i -lt $script:message_entries.Count; $i++) {
                $entry = $script:message_entries[$i]
                if ($filter -and ($entry.Text -notmatch $filter)) {
                    continue
                }
                $lines += @{ t = (get_timestamp_text $entry.Timestamp); dir = $entry.Direction; text = $entry.Text; hit = $entry.Hit }
            }
            return @{ ok = $true; lines = $lines }
        }
        'wave_snapshot' {
            $count = [Math]::Max(1, [int](get_json_prop $req 'count' 500))
            $take = [Math]::Min($count, $script:wave_frame_count)
            $start = $script:wave_frame_count - $take
            $channels = @()
            for ($c = 0; $c -lt $script:wave_ch_values.Count; $c++) {
                $values = @()
                for ($j = $start; $j -lt $script:wave_frame_count; $j++) {
                    $values += $script:wave_ch_values[$c][$j]
                }
                $channels += @{ name = (get_wave_channel_display_name -index $c); values = $values }
            }
            return @{ ok = $true; protocol = $script:wave_protocol; frame_total = $script:wave_frame_total; count = $take; channels = $channels }
        }
        'set_protocol' {
            $protocol = [string](get_json_prop $req 'protocol')
            if ($protocol -notin @('FireWater', 'JustFloat', 'RawData')) {
                return @{ ok = $false; error = 'protocol 必须是 FireWater/JustFloat/RawData' }
            }
            if ($script:wcombo_protocol -and -not $script:wcombo_protocol.IsDisposed) {
                $script:wcombo_protocol.SelectedItem = $protocol
            }
            else {
                $script:wave_protocol = $protocol
                reset_wave_buffers
                reset_wave_decoder
            }
            return @{ ok = $true; protocol = $script:wave_protocol }
        }
        'wave_clear' {
            reset_wave_buffers
            return @{ ok = $true }
        }
        'wave_sim' {
            # 远程开关模拟数据源（走真实接收管线），供 AI/测试无需手点 GUI。
            # 无窗口时强制启用波形解析（关闭时若窗口仍未开则恢复关闭），
            # 否则"关窗即停解析"会把远程采集的数据挡在波形管线之外。
            $on = [bool](get_json_prop $req 'on' $true)
            if ($on) {
                start_wave_sim
                if (-not $script:wave_form -or $script:wave_form.IsDisposed) {
                    $script:wave_enabled = $true
                }
            }
            else {
                stop_wave_sim
                if (-not $script:wave_form -or $script:wave_form.IsDisposed) {
                    $script:wave_enabled = $false
                }
            }
            return @{ ok = $true; sim_on = $script:wave_sim_on; wave_enabled = $script:wave_enabled }
        }
        'record' {
            $on = [bool](get_json_prop $req 'on' $true)
            if ($on) {
                if (-not $script:session_record_writer) { start_session_record }
            }
            else {
                stop_session_record
            }
            return @{ ok = $true; recording = ($null -ne $script:session_record_writer); file = $script:session_record_path }
        }
        'expect' {
            if (-not $script:serial_port -or -not $script:serial_port.IsOpen) {
                return @{ ok = $false; error = '串口未连接' }
            }
            $steps = get_json_prop $req 'steps'
            if (-not $steps) {
                return @{ ok = $false; error = '缺少 steps 数组' }
            }
            $expect_state = @{ decoder = [System.Text.UTF8Encoding]::new($false, $false).GetDecoder(); sb = New-Object System.Text.StringBuilder }
            $result = invoke_expect_steps -steps @($steps) -send_func ${function:send_control_payload} -read_func { param([int]$timeout_ms) read_serial_text_window -serial $script:serial_port -timeout_ms $timeout_ms -state $expect_state }
            return @{ ok = $true; total = $result.total; fail = $result.fail; all_pass = $result.all_pass; steps = $result.steps }
        }
        default {
            return @{ ok = $false; error = ('未知命令: ' + $cmd) }
        }
    }
}

function start_control_listener {
    param([int]$port_number = 9101)

    stop_control_listener
    try {
        $script:control_listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port_number)
        $script:control_listener.Start()
        $script:control_timer = New-Object System.Windows.Forms.Timer
        $script:control_timer.Interval = 120
        $script:control_timer.Add_Tick({ handle_control_tick })
        $script:control_timer.Start()
        add_message_entry -direction 'SYS' -text ('AI 控制通道已开启: tcp://127.0.0.1:{0}（JSON 行协议: ping/status/send/read_lines/wave_snapshot/set_protocol/wave_clear/record/expect/list_ports）' -f $port_number)
    }
    catch {
        $script:control_listener = $null
        write_runtime_log -message ('AI 控制通道启动失败: {0}' -f $_.Exception.Message)
    }
}

function stop_control_listener {
    if ($script:control_timer) {
        try {
            $script:control_timer.Stop()
            $script:control_timer.Dispose()
        }
        catch {
        }
        $script:control_timer = $null
    }
    if ($script:control_listener) {
        try {
            $script:control_listener.Stop()
        }
        catch {
        }
        $script:control_listener = $null
    }
}

function set_hit_pattern {
    param([string]$pattern)

    if ([string]::IsNullOrWhiteSpace($pattern)) {
        $script:hit_regex = $null
        $script:hit_pattern_text = ''
        return
    }
    $script:hit_regex = [System.Text.RegularExpressions.Regex]::new($pattern, [System.Text.RegularExpressions.RegexOptions]'Compiled,IgnoreCase')
    $script:hit_pattern_text = $pattern
}

