# core\serial_io.ps1 — 串口 IO / 端口探测 / 日志与消息管线 / 设置持久化（最先加载）
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function new_font {
    param(
        [string]$name = 'Consolas',
        [float]$size = 10,
        [System.Drawing.FontStyle]$style = [System.Drawing.FontStyle]::Regular
    )
    New-Object System.Drawing.Font($name, $size, $style)
}

function get_timestamp_text {
    param(
        [datetime]$timestamp = (Get-Date)
    )

    $timestamp.ToString('yyyy-MM-dd HH:mm:ss.fff')
}

function reset_rx_decoder {
    $script:rx_decoder = [System.Text.UTF8Encoding]::new($false, $false).GetDecoder()
}

function reset_rx_line_buffer {
    $script:rx_line_buffer = ''
}

function ensure_runtime_log_path {
    if ($script:runtime_log_path) {
        return
    }

    $log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'
    if (-not (Test-Path -LiteralPath $log_dir)) {
        New-Item -ItemType Directory -Path $log_dir | Out-Null
    }

    $script:runtime_log_path = Join-Path -Path $log_dir -ChildPath 'runtime_error.log'
}

function write_runtime_log {
    param(
        [string]$message
    )

    ensure_runtime_log_path
    Add-Content -Path $script:runtime_log_path -Value ('[{0}] {1}' -f (get_timestamp_text), $message) -Encoding UTF8
}

function get_port_display_text {
    param([string]$port_name, [string]$description)

    if ([string]::IsNullOrWhiteSpace($description)) {
        return $port_name
    }

    # 描述通常自带 "(COM6)" 尾巴，去掉避免重复显示
    $desc = [regex]::Replace($description.Trim(), '\s*\(COM\d+\)\s*$', '', 'IgnoreCase')
    if ([string]::IsNullOrWhiteSpace($desc) -or $desc -eq $port_name) {
        return $port_name
    }

    return ('{0} — {1}' -f $port_name, $desc)
}

function get_port_name_from_display {
    param([string]$text)

    if ([string]::IsNullOrWhiteSpace($text)) {
        return ''
    }

    $match = [regex]::Match($text.Trim(), 'COM\d+', 'IgnoreCase')
    if ($match.Success) {
        return $match.Value.ToUpperInvariant()
    }

    return $text.Trim()
}

function get_serial_device_map_sync {
    # WMI 查询 Ports 类设备，返回 @{ 'COM6' = 'USB-SERIAL CH340 (COM6)' }
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
    return $result
}

function test_is_ch340_description {
    # CH340/CH341（WCH 芯片，鲁班猫板载串口）识别
    param([string]$description)

    return ($description -match 'CH34[01]')
}

function pick_auto_connect_port {
    # 多个候选端口时优先选 CH340 设备
    param([string[]]$new_ports, [hashtable]$device_map)

    if ($new_ports.Count -eq 0) {
        return ''
    }

    foreach ($port in $new_ports) {
        $desc = ''
        if ($device_map -and $device_map.ContainsKey($port)) {
            $desc = [string]$device_map[$port]
        }
        if (test_is_ch340_description -description $desc) {
            return $port
        }
    }

    return [string]($new_ports | Select-Object -First 1)
}

function detect_new_ports {
    param([string[]]$current_names, [string[]]$previous_names, [bool]$first_done)

    if (-not $first_done) {
        # 启动后首次刷新只建立基线，已插着的设备不触发自动连接
        return @()
    }
    return @($current_names | Where-Object { $previous_names -notcontains $_ })
}

function refresh_ports {
    # 手动刷新 / 启动初始化：同步查询，立即带出设备描述
    update_port_combobox -device_map (get_serial_device_map_sync)
}

function start_port_probe {
    # 后台 runspace 异步查询，避免 WMI 卡 UI 线程
    if ($script:port_probe_busy) {
        return
    }

    $script:port_probe_busy = $true
    $ps_instance = [powershell]::Create()
    [void]$ps_instance.AddScript($script:port_probe_script)
    $ps_instance.Runspace.ThreadOptions = 'ReuseThread'
    $script:port_probe_ps = $ps_instance
    $script:port_probe_handle = $ps_instance.BeginInvoke()
}

function drain_port_probe {
    if (-not $script:port_probe_busy) {
        return
    }
    if (-not $script:port_probe_handle -or -not $script:port_probe_handle.IsCompleted) {
        return
    }

    $result_map = @{}
    try {
        $output = $script:port_probe_ps.EndInvoke($script:port_probe_handle)
        if ($output -and $output.Count -gt 0) {
            $result_map = [hashtable]$output[0]
        }
    }
    catch {
    }
    try {
        $script:port_probe_ps.Dispose()
    }
    catch {
    }
    $script:port_probe_ps = $null
    $script:port_probe_handle = $null
    $script:port_probe_busy = $false

    update_port_combobox -device_map $result_map
}

function start_port_watch_timer {
    if ($script:port_watch_timer) {
        return
    }

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 2000
    $timer.Add_Tick({
        drain_port_probe
        start_port_probe
    })
    $script:port_watch_timer = $timer
    $timer.Start()
}

function stop_port_watch_timer {
    if ($script:port_watch_timer) {
        try {
            $script:port_watch_timer.Stop()
        }
        catch {
        }
        $script:port_watch_timer.Dispose()
        $script:port_watch_timer = $null
    }
    if ($script:port_probe_ps) {
        try {
            $script:port_probe_ps.Dispose()
        }
        catch {
        }
        $script:port_probe_ps = $null
        $script:port_probe_handle = $null
        $script:port_probe_busy = $false
    }
}

function handle_auto_connect {
    param([string[]]$new_ports, [hashtable]$device_map)

    $target = pick_auto_connect_port -new_ports $new_ports -device_map $device_map
    if ([string]::IsNullOrWhiteSpace($target)) {
        return
    }

    $desc = ''
    if ($device_map -and $device_map.ContainsKey($target)) {
        $desc = [string]$device_map[$target]
    }
    add_message_entry -direction 'SYS' -text ('检测到新串口设备 {0}，尝试自动连接…' -f (get_port_display_text -port_name $target -description $desc))

    for ($i = 0; $i -lt $combobox_port.Items.Count; $i++) {
        if ((get_port_name_from_display -text $combobox_port.Items[$i]) -eq $target) {
            $combobox_port.SelectedIndex = $i
            break
        }
    }

    try {
        connect_serial_port
    }
    catch {
        add_message_entry -direction 'SYS' -text ('自动连接失败: {0}' -f $_.Exception.Message)
    }
}

function get_gui_settings_path {
    return (Join-Path -Path $script:app_root -ChildPath 'gui_settings.txt')
}

function load_gui_settings {
    try {
        if (Test-Path -LiteralPath (get_gui_settings_path)) {
            foreach ($line in (Get-Content -LiteralPath (get_gui_settings_path) -Encoding UTF8)) {
                if ($line -match '^\s*auto_link\s*=\s*(1|true|yes)\s*$') {
                    $checkbox_autolink.Checked = $true
                }
                elseif ($line -match '^\s*auto_link\s*=\s*(0|false|no)\s*$') {
                    $checkbox_autolink.Checked = $false
                }
                elseif ($line -match '^\s*baud\s*=\s*(\d+)\s*$') {
                    if ($combobox_baud) {
                        $combobox_baud.Text = $Matches[1]
                    }
                }
                elseif ($line -match '^\s*port\s*=\s*(COM\d+)\s*$') {
                    # 端口在设备列表刷新后才可能存在，先记下，由 update_port_combobox 恢复
                    $script:saved_port_name = $Matches[1].ToUpperInvariant()
                }
                elseif ($line -match '^\s*rx_format\s*=\s*(ASCII|HEX)\s*$') {
                    if ($combobox_rx_format) {
                        $combobox_rx_format.SelectedItem = $Matches[1]
                    }
                }
                elseif ($line -match '^\s*tx_format\s*=\s*(ASCII|HEX)\s*$') {
                    if ($combobox_tx_format) {
                        $combobox_tx_format.SelectedItem = $Matches[1]
                    }
                }
                elseif ($line -match '^\s*channel_names\s*=\s*(.*)$') {
                    $script:wave_channel_names = New-Object 'System.Collections.Generic.List[string]'
                    foreach ($saved_name in ($Matches[1] -split ',')) {
                        [void]$script:wave_channel_names.Add([string]$saved_name)
                    }
                }
                elseif ($line -match '^\s*theme\s*=\s*(Dark|Light)\s*$') {
                    # 恢复上次主题；赋值会触发 SelectedIndexChanged → apply_theme
                    if ($combobox_theme) {
                        $combobox_theme.SelectedItem = $Matches[1]
                    }
                }
            }
        }
    }
    catch {
    }
}

function save_gui_setting {
    param([string]$key, [string]$value)

    try {
        $path = get_gui_settings_path
        $lines = @()
        if (Test-Path -LiteralPath $path) {
            $lines = @(Get-Content -LiteralPath $path -Encoding UTF8 |
                Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($key) + '\s*=') })
        }
        $lines += ('{0}={1}' -f $key, $value)
        [System.IO.File]::WriteAllLines($path, $lines, [System.Text.UTF8Encoding]::new($false))
    }
    catch {
    }
}

function ensure_log_path {
    if (-not $script:log_file_path) {
        $log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'
        if (-not (Test-Path -LiteralPath $log_dir)) {
            New-Item -ItemType Directory -Path $log_dir | Out-Null
        }

        $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $script:log_file_path = Join-Path -Path $log_dir -ChildPath ('serial_auto_log_{0}.txt' -f $timestamp)
        $label_log_path.Text = '日志: ' + $script:log_file_path
    }

    if (-not $script:log_writer) {
        $encoding = [System.Text.UTF8Encoding]::new($false)
        $stream = [System.IO.FileStream]::new(
            $script:log_file_path,
            [System.IO.FileMode]::Append,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::ReadWrite
        )
        $script:log_writer = [System.IO.StreamWriter]::new($stream, $encoding)
        $script:log_writer.AutoFlush = $false
    }
}

function flush_pending_log_blocks {
    if ($script:pending_log_blocks.Count -le 0) {
        return
    }

    if (-not $checkbox_auto_save.Checked) {
        $script:pending_log_blocks.Clear()
        return
    }

    ensure_log_path
    foreach ($block in $script:pending_log_blocks) {
        $script:log_writer.Write($block)
    }
    $script:pending_log_blocks.Clear()
    $script:log_writer.Flush()
}

function close_auto_log_writer {
    try {
        flush_pending_log_blocks
    }
    catch {
        write_runtime_log -message ('刷新自动日志异常: {0}' -f $_.Exception.Message)
    }

    if ($script:log_writer) {
        try {
            $script:log_writer.Flush()
            $script:log_writer.Close()
            $script:log_writer.Dispose()
        }
        catch {
            write_runtime_log -message ('关闭自动日志异常: {0}' -f $_.Exception.Message)
        }
        finally {
            $script:log_writer = $null
        }
    }
}

function get_status_color {
    # 状态色走主题 token，不再用 DarkRed/DarkGreen 这类固定深色——
    # 深色主题下 DarkGreen(#006400) 落在深底上几乎读不出来。
    param([string]$tone)

    switch ($tone) {
        'ok' { return $script:ui.OkText }
        'warn' { return $script:ui.WarnText }
        'err' { return $script:ui.ErrText }
        'info' { return $script:ui.InfoText }
        default { return $script:ui.TextMain }
    }
}

function update_status {
    # -tone: ok | warn | err | info（默认 info）
    param(
        [string]$text,
        [string]$tone = 'info'
    )

    $label_status.Text = $text
    $label_status.ForeColor = get_status_color -tone $tone
}

function append_text_box {
    param(
        [System.Windows.Forms.TextBoxBase]$text_box,
        [string]$text
    )

    if ([string]::IsNullOrEmpty($text)) {
        return
    }

    if ($text.Length -gt $script:max_textbox_chars) {
        $text = $text.Substring($text.Length - $script:max_textbox_chars)
    }

    if (($text_box.TextLength + $text.Length) -gt $script:max_textbox_chars) {
        $keep_length = $script:max_textbox_chars - $text.Length - 2048
        if ($keep_length -gt 0 -and $text_box.TextLength -gt $keep_length) {
            $text_box.Text = "[V5] 旧显示内容已裁剪，完整内容请看自动日志或导出日志。`r`n" +
                $text_box.Text.Substring($text_box.TextLength - $keep_length)
        }
        else {
            $text_box.Clear()
        }
    }

    $rich_box = $text_box -as [System.Windows.Forms.RichTextBox]
    if ($rich_box) {
        # 颜色来自主题 token，不能硬编码 WhiteSmoke/White——
        # 硬编码会让 Light 主题变成白底白字。
        $color = $script:ui.LogTextRx
        if ($text_box -eq $textbox_tx) {
            $color = $script:ui.LogTextTx
        }

        $block_lines = $text -split "`r`n"
        $appended_any = $false
        foreach ($block_line in $block_lines) {
            if ($block_line.Length -eq 0) {
                continue
            }
            $is_hit = ($script:hit_regex -and $script:hit_regex.IsMatch($block_line) -and ($rich_box -ne $textbox_tx))
            # 每行都显式落一次 SelectionColor：RichTextBox.AppendText 会沿用上一次
            # 的选择色，不重置就会串色
            $rich_box.SelectionStart = $rich_box.TextLength
            $rich_box.SelectionColor = $color
            if ($is_hit) {
                $rich_box.SelectionColor = $script:ui.HitText
            }
            $rich_box.AppendText($block_line + "`r`n")
            $rich_box.SelectionColor = $color
            $appended_any = $true
        }

        if (-not $appended_any) {
            $rich_box.AppendText("`r`n")
        }
        # 高频 AppendText 会把每次追加压入 undo 栈持续膨胀，批量后直接丢弃
        $rich_box.ClearUndo()
        $rich_box.SelectionStart = $rich_box.TextLength
        $rich_box.ScrollToCaret()
        return
    }

    $text_box.AppendText($text)
    $text_box.SelectionStart = $text_box.TextLength
    $text_box.ScrollToCaret()
}

function sanitize_terminal_text {
    param(
        [string]$text
    )

    if ([string]::IsNullOrEmpty($text)) {
        return ''
    }

    $clean = $text -replace "`0", ''
    $clean = [System.Text.RegularExpressions.Regex]::Replace($clean, "\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)", '')
    $clean = [System.Text.RegularExpressions.Regex]::Replace($clean, "\x1B\[[0-?]*[ -/]*[@-~]", '')
    $clean = [System.Text.RegularExpressions.Regex]::Replace($clean, "\x1B[@-_]", '')
    return $clean
}

function decode_serial_bytes {
    param(
        [byte[]]$bytes
    )

    if (-not $bytes -or $bytes.Length -eq 0) {
        return ''
    }

    if (-not $script:rx_decoder) {
        reset_rx_decoder
    }

    $char_count = $script:rx_decoder.GetCharCount($bytes, 0, $bytes.Length)
    if ($char_count -le 0) {
        return ''
    }

    $chars = New-Object char[] $char_count
    [void]$script:rx_decoder.GetChars($bytes, 0, $bytes.Length, $chars, 0)
    return [string]::new($chars)
}

function add_message_entry {
    param(
        [ValidateSet('RX', 'TX', 'SYS')]
        [string]$direction,
        [string]$text,
        [datetime]$timestamp = (Get-Date)
    )

    if ([string]::IsNullOrEmpty($text)) {
        return
    }

    $clean_text = sanitize_terminal_text -text $text
    if ([string]::IsNullOrWhiteSpace($clean_text)) {
        return
    }

    $entry = [pscustomobject]@{
        Timestamp = $timestamp
        Direction = $direction
        Text = $clean_text
        Hit = $false
    }
    [void]$script:message_entries.Add($entry)

    # 上限裁剪：长跑防 message_entries 无限增长（read_lines 只读最近条目，无损）
    if ($script:message_entries.Count -gt $script:max_message_entries) {
        $script:message_entries.RemoveRange(0, 4000)
    }

    if ($script:hit_regex -and $direction -eq 'RX' -and $script:hit_regex.IsMatch($clean_text)) {
        $entry.Hit = $true
    }

    $header = '[{0}] {1}' -f (get_timestamp_text $timestamp), $direction
    $body = $clean_text
    if (-not $body.EndsWith("`r`n")) {
        $body += "`r`n"
    }
    $block = "{0}`r`n{1}`r`n" -f $header, $body

    if ($script:pending_display_entries.Count -ge $script:max_pending_display_entries) {
        $remove_count = [Math]::Min(1000, $script:pending_display_entries.Count)
        $script:pending_display_entries.RemoveRange(0, $remove_count)
        $script:ui_skip_total += $remove_count
    }
    [void]$script:pending_display_entries.Add($entry)

    if ($checkbox_auto_save.Checked -and $direction -ne 'SYS') {
        [void]$script:pending_log_blocks.Add($block)
    }

    if ($direction -eq 'RX') {
        $decoded_fault = test_fault_dump_line -text $clean_text
        if ($decoded_fault) {
            add_message_entry -direction 'SYS' -text ('HardFault 解码: {0}' -f $decoded_fault.decoded)
        }
    }
}

function flush_pending_display_entries {
    param(
        [switch]$DrainAll
    )

    if ($script:pending_display_entries.Count -gt 0) {
        $take_count = $script:pending_display_entries.Count
        if (-not $DrainAll) {
            $take_count = [Math]::Min($take_count, $script:max_display_entries_per_tick)
        }

        $items = $script:pending_display_entries.GetRange(0, $take_count)
        $script:pending_display_entries.RemoveRange(0, $take_count)

        $rx_builder = New-Object System.Text.StringBuilder
        $tx_builder = New-Object System.Text.StringBuilder

        foreach ($entry in $items) {
            $header = '[{0}] {1}' -f (get_timestamp_text $entry.Timestamp), $entry.Direction
            $body = $entry.Text
            if (-not $body.EndsWith("`r`n")) {
                $body += "`r`n"
            }
            $block = "{0}`r`n{1}`r`n" -f $header, $body

            switch ($entry.Direction) {
                'RX' {
                    [void]$rx_builder.Append($block)
                }
                'TX' {
                    [void]$tx_builder.Append($block)
                }
                default {
                    # SYS 只进 RX 区：RX 区实质是「日志区」，TX 区只看用户发送记录。
                    # 双区重复刷启动横幅/控制通道公告既冗余又把 TX 区首屏顶掉。
                    [void]$rx_builder.Append($block)
                }
            }
        }

        append_text_box -text_box $textbox_rx -text $rx_builder.ToString()
        append_text_box -text_box $textbox_tx -text $tx_builder.ToString()
    }

    flush_pending_log_blocks
}

function flush_rx_line_buffer {
    if ([string]::IsNullOrEmpty($script:rx_line_buffer)) {
        return
    }

    $script:rx_line_total++
    add_message_entry -direction 'RX' -text $script:rx_line_buffer
    reset_rx_line_buffer
}

function flush_rx_line_if_idle {
    # 空闲落显：ASCII 模式下无换行的残行原本要攒到 8KB 才强制显示——短消息
    # （不带换行的 AT+OK 之类）会一直看不见。静默超过阈值就把残行就地落成一条 RX。
    # 状态定时器（500ms）每轮调用；正常收发时残行在 ~0.5s 内显示、积压归零，
    # 「积压」指标回归纯异常信号（数据持续流入但格式/波特率不对）。
    param([int]$idle_ms = 400)

    if (-not $script:rx_last_byte_at) {
        return $false
    }
    if ([string]::IsNullOrEmpty($script:rx_line_buffer)) {
        return $false
    }
    if (([datetime]::Now - $script:rx_last_byte_at).TotalMilliseconds -lt $idle_ms) {
        return $false
    }
    flush_rx_line_buffer
    return $true
}

function append_received_ascii_text {
    param(
        [string]$text
    )

    if ([string]::IsNullOrEmpty($text)) {
        return
    }

    # V3.2: 串口 Read() 可能在一行中间返回；这里按换行重组，UI 和日志再批量输出。
    $script:rx_line_buffer += $text
    $normalized = $script:rx_line_buffer -replace "`r`n", "`n" -replace "`r", "`n"
    $parts = [System.Text.RegularExpressions.Regex]::Split($normalized, "`n")
    if ($parts.Count -le 1) {
        if ($script:rx_line_buffer.Length -ge 8192) {
            flush_rx_line_buffer
        }
        return
    }

    for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
        $script:rx_line_total++
        add_message_entry -direction 'RX' -text $parts[$index]
    }

    $script:rx_line_buffer = $parts[$parts.Count - 1]
    if ($script:rx_line_buffer.Length -ge 8192) {
        flush_rx_line_buffer
    }
}

function get_newline_text {
    switch ($combobox_newline.SelectedItem) {
        'CR' { "`r" }
        'LF' { "`n" }
        'CRLF' { "`r`n" }
        default { '' }
    }
}

function get_log_export_text {
    $builder = New-Object System.Text.StringBuilder
    foreach ($entry in $script:message_entries) {
        $header = '[' + (get_timestamp_text $entry.Timestamp) + '] ' + $entry.Direction
        [void]$builder.AppendLine($header)
        [void]$builder.AppendLine($entry.Text)
        [void]$builder.AppendLine('')
    }
    $builder.ToString()
}

function clear_message_entries {
    reset_rx_line_buffer
    $script:message_entries.Clear()
    $script:pending_display_entries.Clear()
    $script:pending_log_blocks.Clear()
    $script:ui_skip_total = 0
    $textbox_rx.Clear()
    $textbox_tx.Clear()
}

function reset_receive_stats {
    $script:rx_bytes_total = [long]0
    $script:rx_line_total = [long]0
    $script:ui_skip_total = [long]0
    $script:rx_last_byte_at = [datetime]::Now
}

function stop_receive_timer {
    if ($script:receive_timer) {
        try {
            $script:receive_timer.Stop()
        }
        catch {
        }

        $script:receive_timer.Dispose()
        $script:receive_timer = $null
    }
}

function stop_status_timer {
    if ($script:status_timer) {
        try {
            $script:status_timer.Stop()
        }
        catch {
        }

        $script:status_timer.Dispose()
        $script:status_timer = $null
    }
}

function update_connected_status {
    # 状态条 500ms 刷新主体（从 timer tick 抽出来便于离线自测）
    if (-not $script:connected) {
        $script:rx_backlog_last = [long]0
        $script:rx_backlog_growth_ticks = 0
        $script:rx_backlog_bytes = [long]0
        return
    }

    $rx_kib = [Math]::Round($script:rx_bytes_total / 1024.0, 1)
    $status_text = ('已连接: {0} @ {1} | RX {2} KiB | 行 {3} | 待显示 {4}' -f
        $script:connected_port_name,
        $script:connected_baud_rate,
        $rx_kib,
        $script:rx_line_total,
        $script:pending_display_entries.Count)
    if ($script:ui_skip_total -gt 0) {
        $status_text += ' | UI跳过 {0}' -f $script:ui_skip_total
    }
    if ($script:wave_enabled) {
        $status_text += ' | 波形帧 {0}' -f $script:wave_frame_total
    }

    # 缓冲积压监控（常显变量 $script:rx_backlog_bytes）：未成行文本 + 波形协议里
    # 等帧尾/等换行的未解析字节 = 「已接收但还没显示」的量。空闲落显保证正常收发时
    # 归零；持续增长 = 数据不是当前协议格式或波特率不对（解码全是乱码凑不出行）。
    $backlog = [long]$script:rx_line_buffer.Length + [long]$script:wave_jf_buffer.Count + [long]$script:wave_line_buffer.Length
    $script:rx_backlog_bytes = $backlog
    $tone = 'ok'
    if ($backlog -gt 0) {
        $delta = $backlog - $script:rx_backlog_last
        $arrow = '→'
        if ($delta -gt 0) {
            $arrow = [string][char]0x2191
            $script:rx_backlog_growth_ticks++
        }
        elseif ($delta -lt 0) {
            $arrow = [string][char]0x2193
            $script:rx_backlog_growth_ticks = 0
        }
        $status_text += (' | 积压 {0:N0} B {1}' -f $backlog, $arrow)
        if ($backlog -ge 65536 -or $script:rx_backlog_growth_ticks -ge 8) {
            $tone = 'err'
        }
        elseif ($backlog -gt 4096 -or $script:rx_backlog_growth_ticks -ge 4) {
            $tone = 'warn'
        }
    }
    else {
        $script:rx_backlog_growth_ticks = 0
        $status_text += ' | 积压 0 B'
    }
    $script:rx_backlog_last = $backlog

    update_status -text $status_text -tone $tone
}

function start_status_timer {
    stop_status_timer

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 500
    $timer.Add_Tick({
        # 无换行残行的空闲落显（见 flush_rx_line_if_idle）：先落显再刷新状态条，
        # 数据停流后积压指标才能归零
        [void](flush_rx_line_if_idle)
        update_connected_status
    })

    $script:status_timer = $timer
    $script:status_timer.Start()
}

function stop_ui_flush_timer {
    if ($script:ui_flush_timer) {
        try {
            $script:ui_flush_timer.Stop()
        }
        catch {
        }

        $script:ui_flush_timer.Dispose()
        $script:ui_flush_timer = $null
    }
}

function start_ui_flush_timer {
    stop_ui_flush_timer

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 100
    $timer.Add_Tick({
        try {
            flush_pending_display_entries
        }
        catch {
            write_runtime_log -message ('UI/日志批量刷新异常: {0}' -f $_.Exception.Message)
        }
    })

    $script:ui_flush_timer = $timer
    $script:ui_flush_timer.Start()
}

function close_serial_port {
    stop_receive_timer
    stop_serial_file_send -reason '串口已断开'
    flush_rx_line_buffer
    flush_pending_display_entries -DrainAll
    stop_status_timer
    close_auto_log_writer
    reset_rx_decoder
    reset_rx_line_buffer

    if ($script:serial_port) {
        try {
            if ($script:serial_port.IsOpen) {
                $script:serial_port.Close()
            }
        }
        catch {
            write_runtime_log -message ('关闭串口异常: {0}' -f $_.Exception.Message)
        }

$script:serial_port.Dispose()
        $script:serial_port = $null
    }

    $script:connected = $false
    sync_connect_buttons
    update_status -text '未连接' -tone 'err'
}

function bytes_to_hex_text {
    param(
        [byte[]]$bytes
    )

    if (-not $bytes -or $bytes.Length -eq 0) {
        return ''
    }

    (($bytes | ForEach-Object { $_.ToString('X2') }) -join ' ')
}

function try_parse_hex_text {
    param(
        [string]$text
    )

    if ([string]::IsNullOrWhiteSpace($text)) {
        return [byte[]]@()
    }

    $normalized = ($text -replace '0x', '').Trim()
    $tokens = $normalized -split '[\s,;:-]+' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $bytes = New-Object 'System.Collections.Generic.List[byte]'

    foreach ($token in $tokens) {
        if ($token -notmatch '^[0-9A-Fa-f]{1,2}$') {
            throw ('HEX 输入无效: ' + $token)
        }

        [void]$bytes.Add([Convert]::ToByte($token, 16))
    }

    $bytes.ToArray()
}

function get_newline_bytes_for_mode {
    # return ,数组 防止 PowerShell 把单元素数组枚举成标量
    param([string]$mode)

    switch ($mode) {
        'CR' { return ,[byte[]](13) }
        'LF' { return ,[byte[]](10) }
        'CRLF' { return ,[byte[]](13, 10) }
        default { return ,[byte[]]@() }
    }
}

function get_send_newline_bytes {
    get_newline_bytes_for_mode -mode ([string]$combobox_newline.SelectedItem)
}

function get_receive_display_text {
    param(
        [byte[]]$bytes
    )

    if ($combobox_rx_format.SelectedItem -eq 'HEX') {
        return (bytes_to_hex_text -bytes $bytes)
    }

    return (decode_serial_bytes -bytes $bytes)
}

function append_received_bytes {
    param(
        [byte[]]$bytes
    )

    if (-not $bytes -or $bytes.Length -eq 0) {
        return
    }

    $script:rx_bytes_total += $bytes.Length
    $script:rx_last_byte_at = [datetime]::Now
    try_consume_wave_bytes -bytes $bytes

    if ($script:session_record_writer) {
        $record = @{
            t = (get_timestamp_text)
            ms = $script:record_stopwatch.ElapsedMilliseconds
            dir = 'DATA'
            b64 = [Convert]::ToBase64String($bytes)
            n = $bytes.Length
        }
        $script:session_record_writer.WriteLine((ConvertTo-Json $record -Compress -Depth 4))
    }

    if ($combobox_rx_format.SelectedItem -eq 'HEX') {
        flush_rx_line_buffer
        $script:rx_line_total++
        add_message_entry -direction 'RX' -text (bytes_to_hex_text -bytes $bytes)
        return
    }

    $text = decode_serial_bytes -bytes $bytes
    append_received_ascii_text -text $text
}

function start_receive_timer {
    stop_receive_timer

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 10
    $timer.Add_Tick({
        if (-not $script:serial_port) {
            return
        }

        if (-not $script:serial_port.IsOpen) {
            return
        }

        try {
            while ($script:serial_port.BytesToRead -gt 0) {
                # V3.2: 接收循环只做快速抽空串口缓冲和轻量解析，UI/日志另由批量刷新定时器处理。
                $chunk_size = [Math]::Min([int]$script:serial_port.BytesToRead, 32768)
                $buffer = New-Object byte[] $chunk_size
                $bytes_read = $script:serial_port.Read($buffer, 0, $buffer.Length)
                if ($bytes_read -le 0) {
                    break
                }

                if ($bytes_read -ne $buffer.Length) {
                    $actual = New-Object byte[] $bytes_read
                    [Array]::Copy($buffer, $actual, $bytes_read)
                    append_received_bytes -bytes $actual
                }
                else {
                    append_received_bytes -bytes $buffer
                }
            }
        }
        catch {
            $message = $_.Exception.Message
            write_runtime_log -message ('串口接收异常: {0}' -f $message)
            add_message_entry -direction 'SYS' -text ('[错误] 串口接收异常: {0}' -f $message)
            update_status -text '接收异常，已断开' -tone 'err'
            close_serial_port
        }
    })

    $script:receive_timer = $timer
    $script:receive_timer.Start()
}

function connect_serial_port {
    if ($script:connected) {
        return
    }

    $port_name = get_port_name_from_display -text $combobox_port.Text
    if ([string]::IsNullOrWhiteSpace($port_name)) {
        throw '请先选择串口。'
    }

    $baud_rate = 0
    if (-not [int]::TryParse($combobox_baud.Text.Trim(), [ref]$baud_rate)) {
        throw '波特率不是有效整数。'
    }

    $data_bits = 0
    if (-not [int]::TryParse($combobox_data_bits.Text.Trim(), [ref]$data_bits)) {
        throw '数据位不是有效整数。'
    }

    $stop_bits = [System.IO.Ports.StopBits]::$($combobox_stop_bits.Text)
    $parity = [System.IO.Ports.Parity]::$($combobox_parity.Text)
    $handshake = [System.IO.Ports.Handshake]::$($combobox_flow_control.Text)

    $serial_port = New-Object System.IO.Ports.SerialPort
    $serial_port.PortName = $port_name
    $serial_port.BaudRate = $baud_rate
    $serial_port.DataBits = $data_bits
    $serial_port.StopBits = $stop_bits
    $serial_port.Parity = $parity
    $serial_port.Handshake = $handshake
    $serial_port.DtrEnable = $checkbox_dtr.Checked
    $serial_port.RtsEnable = $checkbox_rts.Checked
    $serial_port.ReadTimeout = 200
    $serial_port.WriteTimeout = 500
    $serial_port.Encoding = [System.Text.Encoding]::ASCII
    $serial_port.NewLine = "`n"
    $serial_port.ReadBufferSize = 1048576
    $serial_port.WriteBufferSize = 8192

    try {
        $serial_port.Open()
        reset_rx_decoder
        reset_rx_line_buffer
        reset_wave_decoder
        reset_receive_stats
        $script:serial_port = $serial_port
        $script:connected = $true
        $script:connected_port_name = $port_name
        $script:connected_baud_rate = $baud_rate
        ensure_log_path
        start_receive_timer
        start_status_timer
        sync_connect_buttons
        update_status -text ('已连接: {0} @ {1}' -f $port_name, $baud_rate) -tone 'ok'
        add_message_entry -direction 'SYS' -text ('已连接 {0}`r`n参数 {1}/{2}/{3}/{4}/{5}' -f $port_name, $baud_rate, $data_bits, $parity, $stop_bits, $handshake)
    }
    catch {
        $serial_port.Dispose()
        throw
    }
}

function send_current_text {
    if (-not $script:serial_port -or -not $script:serial_port.IsOpen) {
        throw '串口未连接。'
    }

    $tx_format = [string]$combobox_tx_format.SelectedItem
    if ([string]::IsNullOrWhiteSpace($tx_format)) {
        $tx_format = 'ASCII'
    }

    if ($tx_format -eq 'HEX') {
        $payload_bytes = try_parse_hex_text -text $textbox_send.Text
        if ($checkbox_append_newline.Checked) {
            # byte[] + 数组在 PS5.1 可能抛 op_Addition；[object[]] cast 对标量包一层、对数组扁平化
            $payload_bytes = @([object[]]$payload_bytes + [object[]](get_send_newline_bytes))
        }

        if (-not $payload_bytes -or $payload_bytes.Count -eq 0) {
            return
        }

        $script:serial_port.Write([byte[]]$payload_bytes, 0, $payload_bytes.Count)
        $display_text = bytes_to_hex_text -bytes ([byte[]]$payload_bytes)
    }
    else {
        $payload = $textbox_send.Text
        if ($checkbox_append_newline.Checked) {
            $payload += get_newline_text
        }

        if ([string]::IsNullOrEmpty($payload)) {
            return
        }

        $script:serial_port.Write($payload)
        $display_text = $payload -replace "`r", '<CR>' -replace "`n", '<LF>`r`n'
    }

    push_send_history -text $textbox_send.Text
    add_message_entry -direction 'TX' -text $display_text

    if ($checkbox_clear_after_send.Checked) {
        $textbox_send.Clear()
    }
}

# ---------- 发送历史（cmd 风格 ⬆/⬇ 回翻） ----------
# send_history_pos = -1 表示正在编辑新行；⬆ 进入历史后停在最旧一条不回绕，⬇ 走到底恢复草稿
function push_send_history {
    param([string]$text)

    if ([string]::IsNullOrWhiteSpace($text)) {
        return
    }

    if (-not $script:send_history) {
        $script:send_history = New-Object 'System.Collections.Generic.List[string]'
    }

    # 连续重复不重复入栈；栈满丢最旧
    if ($script:send_history.Count -gt 0 -and $script:send_history[$script:send_history.Count - 1] -eq $text) {
        $script:send_history_pos = -1
        return
    }

    [void]$script:send_history.Add($text)
    if ($script:send_history.Count -gt $script:send_history_max) {
        $script:send_history.RemoveAt(0)
    }
    $script:send_history_pos = -1
}

function recall_send_history {
    # direction: -1 = ⬆ 上一条（更旧），+1 = ⬇ 下一条（更新）
    param([int]$direction)

    if (-not $textbox_send -or $textbox_send.IsDisposed) {
        return $false
    }

    $count = 0
    if ($script:send_history) {
        $count = $script:send_history.Count
    }

    if ($count -eq 0) {
        return $false
    }

    if ($direction -lt 0) {
        if ($script:send_history_pos -lt 0) {
            # 第一次 ⬆：记住正在编辑的内容作为草稿，⬇ 走到底时还原
            $script:send_history_draft = $textbox_send.Text
            $script:send_history_pos = $count - 1
        }
        elseif ($script:send_history_pos -gt 0) {
            $script:send_history_pos--
        }
        $textbox_send.Text = $script:send_history[$script:send_history_pos]
    }
    else {
        if ($script:send_history_pos -lt 0) {
            return $false
        }

        $script:send_history_pos++
        if ($script:send_history_pos -ge $count) {
            $script:send_history_pos = -1
            $textbox_send.Text = $script:send_history_draft
        }
        else {
            $textbox_send.Text = $script:send_history[$script:send_history_pos]
        }
    }

    $textbox_send.SelectionStart = $textbox_send.TextLength
    $textbox_send.ScrollToCaret()
    return $true
}

# ---------- 日志行悬停格式互转 ----------
# HEX 行（HEX 接收模式的日志）→ 解码成 文本/十进制/float32(小端)；
# 文本行（ASCII 接收模式）→ 给出 UTF-8 HEX。预览字节超上限截断加省略号。
function get_line_alt_format_tooltip {
    param([string]$line)

    if ([string]::IsNullOrWhiteSpace($line)) {
        return ''
    }

    $trimmed = $line.Trim()
    $preview_limit = 64

    # 整行都是 2 位十六进制对（HEX 模式日志的形状）；单个 2 字符 token 当文本
    # 处理（"OK"/"AB" 之类短词做 hex 误判率太高）
    if ($trimmed.Length -ge 5 -and $trimmed -match '^[0-9A-Fa-f]{2}(?:[\s,;]+[0-9A-Fa-f]{2})+$') {
        try {
            $bytes = try_parse_hex_text -text $trimmed
        }
        catch {
            return ''
        }

        if (-not $bytes -or $bytes.Length -eq 0) {
            return ''
        }

        $show_count = [Math]::Min($bytes.Length, $preview_limit)
        $preview = New-Object byte[] $show_count
        [Array]::Copy($bytes, $preview, $show_count)

        $lines = New-Object System.Collections.Generic.List[string]
        $builder = New-Object System.Text.StringBuilder
        foreach ($byte_value in $preview) {
            if ($byte_value -ge 0x20 -and $byte_value -le 0x7E) {
                [void]$builder.Append([char]$byte_value)
            }
            elseif ($byte_value -eq 0x0D) {
                [void]$builder.Append('<CR>')
            }
            elseif ($byte_value -eq 0x0A) {
                [void]$builder.Append('<LF>')
            }
            else {
                [void]$builder.Append([string][char]0x00B7)
            }
        }
        $suffix = ''
        if ($bytes.Length -gt $show_count) {
            $suffix = ' …'
        }
        [void]$lines.Add(('文本(UTF-8): ' + $builder.ToString() + $suffix))
        [void]$lines.Add(('十进制: ' + (($preview | ForEach-Object { $_ }) -join ' ') + $suffix))

        if (($bytes.Length % 4) -eq 0) {
            $float_count = [Math]::Min([int]($bytes.Length / 4), 16)
            $floats = New-Object System.Collections.Generic.List[string]
            for ($index = 0; $index -lt $float_count; $index++) {
                [void]$floats.Add(([BitConverter]::ToSingle($bytes, $index * 4)).ToString('G6'))
            }
            [void]$lines.Add(('float32(小端): ' + ($floats -join ', ')))
        }

        return ($lines -join "`n")
    }

    # 文本行 → UTF-8 HEX
    $text_bytes = [System.Text.Encoding]::UTF8.GetBytes($trimmed)
    if ($text_bytes.Length -eq 0) {
        return ''
    }

    $show_count = [Math]::Min($text_bytes.Length, $preview_limit)
    $preview = New-Object byte[] $show_count
    [Array]::Copy($text_bytes, $preview, $show_count)
    $suffix = ''
    if ($text_bytes.Length -gt $show_count) {
        $suffix = ' …'
    }
    return ('HEX: ' + (bytes_to_hex_text -bytes $preview) + $suffix)
}

function get_log_box_line_text {
    # 取 RichTextBox 第 line_no 行原文（逻辑行；日志框 WordWrap=false，显示行=逻辑行）
    param($box, [int]$line_no)

    if ($line_no -lt 0) {
        return ''
    }

    $start = $box.GetFirstCharIndexFromLine($line_no)
    if ($start -lt 0) {
        return ''
    }

    $text = $box.Text
    if ($start -ge $text.Length) {
        return ''
    }

    $slice = $text.Substring($start)
    $newline_index = $slice.IndexOf("`n")
    if ($newline_index -ge 0) {
        $slice = $slice.Substring(0, $newline_index)
    }
    return $slice.TrimEnd("`r")
}

function update_log_hover_tooltip {
    # SetToolTip 同值不触发重新弹出；先清两个框再设目标框，换行悬停即重弹
    param($box, [string]$text)

    if ($text -ceq $script:log_hover_state.Text) {
        return
    }

    $script:log_hover_state.Text = $text
    $script:log_hover_tip.SetToolTip($textbox_rx, $null)
    $script:log_hover_tip.SetToolTip($textbox_tx, $null)
    if ($text) {
        $script:log_hover_tip.SetToolTip($box, $text)
    }
}

function handle_log_hover_move {
    param($box, [System.Drawing.Point]$location)

    if (-not $script:log_hover_tip -or -not $script:log_hover_state) {
        return
    }

    $char_index = $box.GetCharIndexFromPosition($location)
    $line_no = $box.GetLineFromCharIndex($char_index)
    if ($line_no -eq $script:log_hover_state.Line) {
        return
    }
    $script:log_hover_state.Line = $line_no

    # 条目结构 = 头行 '[时间] RX/TX/SYS' + 正文行。悬停头行时解析它的正文行。
    $line_text = get_log_box_line_text -box $box -line_no $line_no
    $body = $line_text
    if ($line_text -match '^\[[^\]]+\] (RX|TX|SYS)$') {
        $body = get_log_box_line_text -box $box -line_no ($line_no + 1)
    }

    update_log_hover_tooltip -box $box -text (get_line_alt_format_tooltip -line $body)
}

function handle_log_hover_leave {
    if ($script:log_hover_state) {
        $script:log_hover_state.Line = -1
        $script:log_hover_state.Text = ''
    }
    if ($script:log_hover_tip) {
        $script:log_hover_tip.SetToolTip($textbox_rx, $null)
        $script:log_hover_tip.SetToolTip($textbox_tx, $null)
    }
}

# ---------- 日志查找栏（Ctrl+F）：文本搜索 + 时间戳定位 ----------
function test_find_query_is_timestamp {
    # 时间戳形态：HH:mm(.ss)?(.fff)? / yyyy-MM-dd / yyyy-MM-dd HH:mm(.ss)?(.fff)?
    param([string]$query)

    $q = $query.Trim()
    if ($q.Length -eq 0) {
        return $false
    }

    if ($q -match '^\d{1,2}:\d{2}(:\d{2})?(\.\d{1,3})?$') {
        return $true
    }
    if ($q -match '^\d{4}-\d{2}-\d{2}( \d{1,2}:\d{2}(:\d{2})?(\.\d{1,3})?)?$') {
        return $true
    }
    return $false
}

function parse_find_timestamp {
    # 把查询解析成 datetime；只给时间时日期先占位为今天，跳转函数再用日志首条日期校正。
    # 非法时间（25:99）返回 $null，调用方回退为普通文本搜索。
    param([string]$query)

    $q = $query.Trim()
    $formats = @('yyyy-MM-dd HH:mm:ss.fff', 'yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd HH:mm', 'yyyy-MM-dd', 'HH:mm:ss.fff', 'HH:mm:ss', 'HH:mm')
    $provider = [System.Globalization.CultureInfo]::InvariantCulture

    foreach ($format in $formats) {
        $parsed = [datetime]::MinValue
        if ([datetime]::TryParseExact($q, $format, $provider, [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
            return $parsed
        }
    }
    return $null
}

function find_log_timestamp_jump {
    # 在 RX 日志头行 [yyyy-MM-dd HH:mm:ss.fff] 里找时间满足条件的条目并选中。
    # -backward 时找 < 查询时间的最近一条；到头/尾回绕。返回 @{ Total; Found; Ordinal; Time; Pos } 或 $null。
    param([string]$query, [bool]$backward)

    if (-not $textbox_rx -or $textbox_rx.IsDisposed) {
        return $null
    }

    $target = parse_find_timestamp -query $query
    if ($null -eq $target) {
        return $null
    }

    $text = $textbox_rx.Text
    $header_matches = [System.Text.RegularExpressions.Regex]::Matches($text, '\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] ')
    if ($header_matches.Count -eq 0) {
        return @{ Total = 0; Found = $false }
    }

    # 只给了时刻没给日期：日期取日志首条（日志通常跨天续跑时直接搜时刻更符合直觉）
    if ($query -notmatch '\d{4}-\d{2}-\d{2}') {
        try {
            $first_ts = [datetime]::ParseExact($header_matches[0].Groups[1].Value, 'yyyy-MM-dd HH:mm:ss.fff', [System.Globalization.CultureInfo]::InvariantCulture)
            $target = $first_ts.Date.Add($target.TimeOfDay)
        }
        catch {
        }
    }

    $provider = [System.Globalization.CultureInfo]::InvariantCulture
    $all = New-Object System.Collections.Generic.List[object]
    $candidates = New-Object System.Collections.Generic.List[object]
    foreach ($m in $header_matches) {
        $ts = [datetime]::MinValue
        if (-not [datetime]::TryParseExact($m.Groups[1].Value, 'yyyy-MM-dd HH:mm:ss.fff', $provider, [System.Globalization.DateTimeStyles]::None, [ref]$ts)) {
            continue
        }
        $item = @($m.Index, $ts)
        [void]$all.Add($item)
        if ($backward) {
            if ($ts -lt $target) {
                [void]$candidates.Add($item)
            }
        }
        else {
            if ($ts -ge $target) {
                [void]$candidates.Add($item)
            }
        }
    }

    if ($candidates.Count -eq 0) {
        return @{ Total = $all.Count; Found = $false }
    }

    $sel_start = $textbox_rx.SelectionStart
    $sel_end = $sel_start + $textbox_rx.SelectionLength
    $chosen = $null
    if (-not $backward) {
        foreach ($item in $candidates) {
            if ($item[0] -ge $sel_end) {
                $chosen = $item
                break
            }
        }
        if ($null -eq $chosen) {
            $chosen = $candidates[0]
        }
    }
    else {
        for ($i = $candidates.Count - 1; $i -ge 0; $i--) {
            if ($candidates[$i][0] -lt $sel_start) {
                $chosen = $candidates[$i]
                break
            }
        }
        if ($null -eq $chosen) {
            $chosen = $candidates[$candidates.Count - 1]
        }
    }

    $ordinal = $all.IndexOf($chosen) + 1
    $textbox_rx.SelectionStart = $chosen[0]
    $textbox_rx.SelectionLength = 20
    return @{ Total = $all.Count; Found = $true; Ordinal = $ordinal; Time = $chosen[1]; Pos = $chosen[0] }
}

function find_log_text_jump {
    # 普通文本搜索（不分大小写），到尾回绕；-backward 反向。返回 @{ Total; Found; Ordinal; Pos } 或 $null。
    param([string]$query, [bool]$backward)

    if (-not $textbox_rx -or $textbox_rx.IsDisposed -or $query.Length -eq 0) {
        return $null
    }

    $text = $textbox_rx.Text
    $escaped = [System.Text.RegularExpressions.Regex]::Escape($query)
    $total = [System.Text.RegularExpressions.Regex]::Matches($text, $escaped, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase).Count

    $sel_start = $textbox_rx.SelectionStart
    $sel_end = $sel_start + $textbox_rx.SelectionLength
    $pos = -1
    if ($backward) {
        if ($sel_start -gt 0) {
            $head = $text.Substring(0, $sel_start)
            $pos = $head.LastIndexOf($query, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($pos -lt 0) {
            $pos = $text.LastIndexOf($query, [System.StringComparison]::OrdinalIgnoreCase)
        }
    }
    else {
        if ($sel_end -lt $text.Length) {
            $pos = $text.IndexOf($query, $sel_end, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($pos -lt 0) {
            $pos = $text.IndexOf($query, [System.StringComparison]::OrdinalIgnoreCase)
        }
    }

    if ($pos -lt 0) {
        return @{ Total = $total; Found = $false }
    }

    $head = $text.Substring(0, $pos)
    $ordinal = [System.Text.RegularExpressions.Regex]::Matches($head, $escaped, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase).Count + 1

    $textbox_rx.SelectionStart = $pos
    $textbox_rx.SelectionLength = $query.Length
    return @{ Total = $total; Found = $true; Ordinal = $ordinal; Pos = $pos }
}

# ---------- 查找高亮与视口居中 ----------
function get_find_highlight_backcolors {
    # 全部命中用弱底色、当前命中用强底色；深浅主题各一套
    if ($script:ui_is_dark) {
        return @{
            All = [System.Drawing.Color]::FromArgb(72, 72, 26)
            Current = [System.Drawing.Color]::FromArgb(134, 98, 12)
        }
    }
    return @{
        All = [System.Drawing.Color]::FromArgb(255, 238, 168)
        Current = [System.Drawing.Color]::FromArgb(255, 198, 94)
    }
}

function clear_find_highlights {
    # 清掉上次铺的高亮底色（位置记录在 find_highlight_ranges）。
    # 注意必须用【字面 RGB】复位：SelectionBackColor 存进 RTF 颜色表，系统色
    # （如默认 Control/Window）会被存成 White，深色日志框上会留白条。
    if (-not $textbox_rx -or $textbox_rx.IsDisposed) {
        return
    }

    $clear_color = [System.Drawing.Color]::FromArgb($textbox_rx.BackColor.R, $textbox_rx.BackColor.G, $textbox_rx.BackColor.B)
    foreach ($range in $script:find_highlight_ranges) {
        $textbox_rx.SelectionStart = $range[0]
        $textbox_rx.SelectionLength = $range[1]
        $textbox_rx.SelectionBackColor = $clear_color
    }
    $script:find_highlight_ranges.Clear()
    # 插入点格式复位：防止新追加的行继承高亮底色
    $textbox_rx.SelectionStart = $textbox_rx.TextLength
    $textbox_rx.SelectionLength = 0
    $textbox_rx.SelectionBackColor = $clear_color
}

function apply_find_highlights {
    # 把当前查询的全部命中铺上弱底色（上限 800 处，1MB 级日志防卡）
    param([string]$query)

    clear_find_highlights
    if ([string]::IsNullOrEmpty($query)) {
        return
    }
    if (-not $textbox_rx -or $textbox_rx.IsDisposed) {
        return
    }

    $text = $textbox_rx.Text
    $colors = get_find_highlight_backcolors
    $matches_list = [System.Text.RegularExpressions.Regex]::Matches($text, [System.Text.RegularExpressions.Regex]::Escape($query), [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $applied = 0
    foreach ($m in $matches_list) {
        if ($applied -ge 800) {
            break
        }
        $textbox_rx.SelectionStart = $m.Index
        $textbox_rx.SelectionLength = $m.Length
        $textbox_rx.SelectionBackColor = $colors.All
        [void]$script:find_highlight_ranges.Add(@($m.Index, $m.Length))
        $applied++
    }
}

function center_log_view_to_char {
    # 把 char_index 所在行滚到视口中间（ScrollToCaret 只会贴到顶/底，观感差）。
    # 无句柄（无头自测）时退回 ScrollToCaret。
    param([int]$char_index)

    if (-not $textbox_rx -or $textbox_rx.IsDisposed) {
        return
    }

    if (-not $textbox_rx.IsHandleCreated -or -not ('V6Native' -as [type])) {
        $textbox_rx.SelectionStart = $char_index
        $textbox_rx.SelectionLength = 0
        $textbox_rx.ScrollToCaret()
        return
    }

    $line_no = $textbox_rx.GetLineFromCharIndex($char_index)
    # EM_GETFIRSTVISIBLELINE
    $first_line = [int][V6Native]::SendMessage($textbox_rx.Handle, 0x00CE, [IntPtr]::Zero, [IntPtr]::Zero)
    $line_height = $textbox_rx.Font.GetHeight()
    $visible = [Math]::Max(3, [int]($textbox_rx.ClientSize.Height / $line_height) - 1)
    $delta = $line_no - [int]($visible / 2) - $first_line
    if ($delta -ne 0) {
        # EM_LINESCROLL：wParam = 水平字符数，lParam = 垂直行数（带符号）
        [void][V6Native]::SendMessage($textbox_rx.Handle, 0x00B6, [IntPtr]::Zero, [IntPtr]$delta)
    }
}

function invoke_log_find {
    # 查找栏回车/按钮/输入防抖的统一入口：像时间戳就按时间定位，否则按文本搜。
    # 命中处高亮（当前命中强色 + 全部命中弱色），并把行滚到视口中间。
    # -restart = 输入过程中触发的即时搜索：从文档头重新匹配。
    param([bool]$backward = $false, [bool]$restart = $false)

    if (-not $script:find_box -or $script:find_box.IsDisposed) {
        return
    }
    if (-not $textbox_rx -or $textbox_rx.IsDisposed) {
        return
    }

    if (-not $restart -and $script:find_live_timer) {
        # 显式导航（Enter / 上一个下一个 / F3）时取消还没触发的即时搜索：
        # 不然 150ms 内的防抖 tick 会把刚跳过去的新位置拽回文档头
        $script:find_live_timer.Stop()
    }

    $query = [string]$script:find_box.Text
    if ([string]::IsNullOrWhiteSpace($query)) {
        clear_find_highlights
        if ($script:find_label_count -and -not $script:find_label_count.IsDisposed) {
            $script:find_label_count.Text = ''
        }
        return
    }

    if ($restart) {
        $textbox_rx.SelectionStart = 0
        $textbox_rx.SelectionLength = 0
    }

    $colors = get_find_highlight_backcolors
    $label_text = ''
    if (test_find_query_is_timestamp -query $query) {
        $result = find_log_timestamp_jump -query $query -backward:$backward
        if ($null -eq $result) {
            # 时间格式合法但解析失败（25:99）→ 回退文本搜索
            $result = find_log_text_jump -query $query -backward:$backward
            if ($result -and $result.Found) {
                apply_find_highlights -query $query
                $textbox_rx.SelectionStart = $result.Pos
                $textbox_rx.SelectionLength = $query.Length
                $textbox_rx.SelectionBackColor = $colors.Current
                center_log_view_to_char -char_index $result.Pos
                $label_text = ('第 {0}/{1} 处' -f $result.Ordinal, $result.Total)
            }
            else {
                clear_find_highlights
                $label_text = '无匹配'
            }
        }
        elseif ($result.Found) {
            # 时间模式只强调当前头行：候选往往铺满整个日志，铺弱色反而看不清
            clear_find_highlights
            $textbox_rx.SelectionStart = $result.Pos
            $textbox_rx.SelectionLength = 20
            $textbox_rx.SelectionBackColor = $colors.Current
            [void]$script:find_highlight_ranges.Add(@($result.Pos, 20))
            center_log_view_to_char -char_index $result.Pos
            $label_text = ('条目 {0}/{1} @ {2:HH:mm:ss.fff}' -f $result.Ordinal, $result.Total, $result.Time)
        }
        else {
            clear_find_highlights
            $label_text = ('{0} 之后无条目（共 {1} 条）' -f $query.Trim(), $result.Total)
        }
    }
    else {
        $result = find_log_text_jump -query $query -backward:$backward
        if ($result -and $result.Found) {
            apply_find_highlights -query $query
            $textbox_rx.SelectionStart = $result.Pos
            $textbox_rx.SelectionLength = $query.Length
            $textbox_rx.SelectionBackColor = $colors.Current
            center_log_view_to_char -char_index $result.Pos
            $label_text = ('第 {0}/{1} 处' -f $result.Ordinal, $result.Total)
        }
        else {
            clear_find_highlights
            $label_text = '无匹配'
        }
    }

    if ($script:find_label_count -and -not $script:find_label_count.IsDisposed) {
        $script:find_label_count.Text = $label_text
    }
}

function toggle_find_panel {
    param([bool]$show = (-not $script:find_panel.Visible))

    if (-not $script:find_panel -or $script:find_panel.IsDisposed) {
        return
    }

    $script:find_panel.Visible = $show
    if ($show) {
        [void]$script:find_box.Focus()
        $script:find_box.SelectAll()
    }
    else {
        clear_find_highlights
        if ($script:find_live_timer) {
            $script:find_live_timer.Stop()
        }
    }
}

# ---------- 文件发送（分块定时器，UI 不卡死；发送中再点 = 停止） ----------
function update_file_send_ui {
    $state = $script:file_send_state
    if ($button_send_file -and -not $button_send_file.IsDisposed) {
        if ($state) {
            $button_send_file.Text = '停止'
        }
        else {
            $button_send_file.Text = '文件发送'
        }
    }
}

function stop_serial_file_send {
    param([string]$reason)

    $state = $script:file_send_state
    if (-not $state) {
        return
    }

    $script:file_send_state = $null
    if ($state.Timer) {
        try {
            $state.Timer.Stop()
            $state.Timer.Dispose()
        }
        catch {
        }
    }

    if ($reason -ne '完成') {
        add_message_entry -direction 'SYS' -text ('[文件发送中断] {0}: {1}（已发 {2:N0} / {3:N0} 字节）' -f $state.Name, $reason, $state.Pos, $state.Bytes.Length)
    }

    update_file_send_ui
}

function tick_serial_file_send {
    $state = $script:file_send_state
    if (-not $state) {
        return
    }

    if (-not $script:serial_port -or -not $script:serial_port.IsOpen) {
        stop_serial_file_send -reason '串口已断开'
        return
    }

    try {
        $chunk_len = [Math]::Min($state.Chunk, $state.Bytes.Length - $state.Pos)
        $chunk = New-Object byte[] $chunk_len
        [Array]::Copy($state.Bytes, $state.Pos, $chunk, 0, $chunk_len)
        $script:serial_port.Write($chunk, 0, $chunk_len)
        $state.Pos += $chunk_len

        if ($state.Pos -ge $state.Bytes.Length) {
            stop_serial_file_send -reason '完成'
            update_status -text ('文件发送完成: {0}（{1:N0} 字节）' -f $state.Name, $state.Bytes.Length) -tone 'ok'
        }
        else {
            update_status -text ('文件发送中 {0}: {1:N1} / {2:N1} KiB' -f $state.Name, ($state.Pos / 1024.0), ($state.Bytes.Length / 1024.0)) -tone 'info'
        }
    }
    catch {
        $message = $_.Exception.Message
        stop_serial_file_send -reason ('写入失败: ' + $message)
        update_status -text ('文件发送失败: ' + $message) -tone 'err'
    }
}

function start_serial_file_send {
    param([string]$path)

    if (-not $script:serial_port -or -not $script:serial_port.IsOpen) {
        throw '串口未连接。'
    }

    if ($script:file_send_state) {
        return
    }

    $bytes = [System.IO.File]::ReadAllBytes($path)
    if (-not $bytes -or $bytes.Length -eq 0) {
        throw '文件为空，没有可发送的内容。'
    }

    $state = @{
        Path = $path
        Name = (Split-Path -Leaf $path)
        Bytes = $bytes
        Pos = [long]0
        # 4 KiB/20ms ≈ 上限 200 KiB/s：高速率下秒发，低速率由 Write 内部超时背压
        Chunk = 4096
        Timer = $null
    }

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 20
    $timer.Add_Tick({ tick_serial_file_send })
    $state.Timer = $timer
    $script:file_send_state = $state
    $timer.Start()

    add_message_entry -direction 'TX' -text ('[文件发送] {0}（{1:N0} 字节，原样字节流）' -f $state.Name, $bytes.Length)
    update_file_send_ui
}

