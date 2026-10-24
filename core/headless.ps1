# core\headless.ps1 — 无头捕获 / 触发 / 期望测试 / 录制回放
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function get_json_prop {
    param($object, [string]$name, $default_value = $null)

    if ($null -eq $object) {
        return $default_value
    }
    if ($object -is [System.Collections.IDictionary]) {
        if ($object.Contains($name)) {
            return $object[$name]
        }
        return $default_value
    }
    $prop = $object.PSObject.Properties[$name]
    if ($prop) {
        return $prop.Value
    }
    return $default_value
}

function new_trigger_engine {
    param([string]$config_file, [string]$events_dir)

    if (-not (Test-Path -LiteralPath $config_file)) {
        throw ('触发配置文件不存在: ' + $config_file)
    }

    $engine = @{
        triggers = @()
        events_dir = $events_dir
        hits = 0
    }

    $config = Get-Content -LiteralPath $config_file -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in @($config)) {
        $pattern = [string](get_json_prop $item 'pattern')
        if ([string]::IsNullOrEmpty($pattern)) {
            continue
        }
        $before = [Math]::Max(1, [int](get_json_prop $item 'before_lines' 20))
        $after = [Math]::Max(1, [int](get_json_prop $item 'after_lines' 50))
        $engine.triggers += @{
            name = [string](get_json_prop $item 'name' ('trigger_{0}' -f ($engine.triggers.Count + 1)))
            regex = [System.Text.RegularExpressions.Regex]::new($pattern, [System.Text.RegularExpressions.RegexOptions]'Compiled,IgnoreCase')
            before = $before
            after = $after
            once = [bool](get_json_prop $item 'once' $true)
            context = New-Object 'System.Collections.Generic.List[string]'
            segment = $null
            after_remaining = 0
            hit_line = ''
            done = $false
        }
    }

    if (-not (Test-Path -LiteralPath $events_dir)) {
        New-Item -ItemType Directory -Path $events_dir -Force | Out-Null
    }
    return $engine
}

function feed_trigger_line {
    param($engine, [string]$line, [long]$ms)

    $events = @()
    if (-not $engine -or @($engine.triggers).Count -eq 0) {
        return $events
    }

    foreach ($trigger in $engine.triggers) {
        if ($trigger.done) {
            continue
        }

        if ($null -eq $trigger.segment) {
            [void]$trigger.context.Add($line)
            while ($trigger.context.Count -gt $trigger.before) {
                $trigger.context.RemoveAt(0)
            }
            if ($trigger.regex.IsMatch($line)) {
                $trigger.hit_line = $line
                $trigger.segment = New-Object 'System.Collections.Generic.List[string]'
                foreach ($context_line in $trigger.context) {
                    [void]$trigger.segment.Add($context_line)
                }
                $trigger.after_remaining = $trigger.after
            }
        }
        else {
            [void]$trigger.segment.Add($line)
            $trigger.after_remaining--
        }

        if (($null -ne $trigger.segment) -and ($trigger.after_remaining -le 0)) {
            $file_path = Join-Path $engine.events_dir ('{0}_{1:yyyyMMdd_HHmmss_fff}.txt' -f $trigger.name, (Get-Date))
            $header = '# 触发: {0}  时间: {1}  命中行: {2}' -f $trigger.name, (get_timestamp_text), $trigger.hit_line
            [System.IO.File]::WriteAllLines($file_path, @($header) + $trigger.segment.ToArray(), [System.Text.UTF8Encoding]::new($false))
            $events += @{ name = $trigger.name; text = $trigger.hit_line; file = $file_path }
            $engine.hits++
            $trigger.segment = $null
            if ($trigger.once) {
                $trigger.done = $true
            }
        }
    }

    return $events
}

function read_serial_text_window {
    param(
        [System.IO.Ports.SerialPort]$serial,
        [int]$timeout_ms,
        [hashtable]$state
    )

    $deadline = [datetime]::Now.AddMilliseconds($timeout_ms)
    while ([datetime]::Now -lt $deadline) {
        if ($serial.BytesToRead -gt 0) {
            $read_size = [Math]::Min($serial.BytesToRead, 32768)
            $buffer = New-Object byte[] $read_size
            $read_n = $serial.Read($buffer, 0, $buffer.Length)
            if ($read_n -gt 0) {
                if ($state.decoder) {
                    $char_count = $state.decoder.GetCharCount($buffer, 0, $read_n)
                    if ($char_count -gt 0) {
                        $chars = New-Object char[] $char_count
                        [void]$state.decoder.GetChars($buffer, 0, $read_n, $chars, 0)
                        [void]$state.sb.Append([string]::new($chars))
                    }
                }
                else {
                    [void]$state.sb.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $read_n))
                }
            }
        }
        else {
            Start-Sleep -Milliseconds 10
        }
    }
    $text = $state.sb.ToString()
    [void]$state.sb.Clear()
    return $text
}

function invoke_expect_steps {
    param(
        [object[]]$steps,
        [scriptblock]$send_func,
        [scriptblock]$read_func,
        [int]$default_timeout_ms = 3000
    )

    $results = @()
    foreach ($step in $steps) {
        $name = [string](get_json_prop $step 'name' ('step_{0}' -f ($results.Count + 1)))
        $expect = [string](get_json_prop $step 'expect')
        $timeout_ms = [int](get_json_prop $step 'timeout_ms' $default_timeout_ms)

        $send_text = $null
        if (get_json_prop $step 'send') {
            $as_hex = [bool](get_json_prop $step 'hex' $false)
            $newline_mode = [string](get_json_prop $step 'newline' 'CRLF')
            & $send_func -text ([string](get_json_prop $step 'send')) -as_hex $as_hex -newline_mode $newline_mode
            $send_text = [string](get_json_prop $step 'send')
        }

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $received = ''
        $pass = $false
        $matched_text = $null
        if ($expect) {
            $received = & $read_func -timeout_ms $timeout_ms
            if ($received -match $expect) {
                $pass = $true
                $matched_text = $Matches[0]
            }
        }
        else {
            if (-not (get_json_prop $step 'send')) {
                & $read_func -timeout_ms $timeout_ms | Out-Null
            }
            $pass = $true
        }

        $received_tail = $received
        if ($received_tail.Length -gt 400) {
            $received_tail = $received_tail.Substring($received_tail.Length - 400)
        }

        $results += [pscustomobject]@{
            name = $name
            send = $send_text
            expect = $expect
            pass = $pass
            elapsed_ms = [int]$sw.ElapsedMilliseconds
            matched = $matched_text
            received_tail = $received_tail
        }
    }

    $fail_count = @($results | Where-Object { -not $_.pass }).Count
    return [pscustomobject]@{
        steps = $results
        total = $results.Count
        fail = $fail_count
        all_pass = ($fail_count -eq 0)
    }
}

function send_control_payload {
    param([string]$text, [bool]$as_hex = $false, [string]$newline_mode = 'CRLF')

    if (-not $script:serial_port -or -not $script:serial_port.IsOpen) {
        throw '串口未连接。'
    }

    if ($as_hex) {
        $payload_bytes = try_parse_hex_text -text $text
        $newline_bytes = get_newline_bytes_for_mode -mode $newline_mode
        if ($newline_bytes.Count -gt 0) {
            # [object[]] cast 对标量包一层、对数组扁平化，避免 byte[]+数组 的 op_Addition
            $payload_bytes = @([object[]]$payload_bytes + [object[]]$newline_bytes)
        }
        if ($payload_bytes.Count -gt 0) {
            $script:serial_port.Write([byte[]]$payload_bytes, 0, $payload_bytes.Count)
        }
        $display_text = bytes_to_hex_text -bytes ([byte[]]$payload_bytes)
    }
    else {
        $payload = [string]$text
        switch ($newline_mode) {
            'CR' { $payload += "`r" }
            'LF' { $payload += "`n" }
            'CRLF' { $payload += "`r`n" }
        }
        if ($payload.Length -gt 0) {
            $script:serial_port.Write($payload)
        }
        $display_text = $payload -replace "`r", '<CR>' -replace "`n", '<LF>`r`n'
    }

    add_message_entry -direction 'TX' -text $display_text
}

function start_session_record {
    if ($script:session_record_writer) {
        return
    }

    $log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'
    if (-not (Test-Path -LiteralPath $log_dir)) {
        New-Item -ItemType Directory -Path $log_dir | Out-Null
    }
    $script:session_record_path = Join-Path $log_dir ('session_record_{0}.jsonl' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    $script:session_record_writer = [System.IO.StreamWriter]::new($script:session_record_path, $false, [System.Text.UTF8Encoding]::new($false))
    $script:session_record_writer.AutoFlush = $true
    $script:record_stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    add_message_entry -direction 'SYS' -text ('会话录制开始: {0}' -f $script:session_record_path)
}

function stop_session_record {
    if ($script:session_record_writer) {
        try {
            $script:session_record_writer.Flush()
            $script:session_record_writer.Dispose()
        }
        catch {
        }
        $script:session_record_writer = $null
        add_message_entry -direction 'SYS' -text '会话录制结束。'
    }
}

function handle_control_tick {
    try {
        $listener = $script:control_listener
        if (-not $listener -or -not $listener.Pending()) {
            return
        }

        $client = $listener.AcceptTcpClient()
        try {
            $client.ReceiveTimeout = 300
            $stream = $client.GetStream()
            $buffer = New-Object byte[] 65536
            $builder = New-Object System.Text.StringBuilder
            $deadline = [datetime]::Now.AddMilliseconds(250)
            while ([datetime]::Now -lt $deadline) {
                if ($stream.DataAvailable) {
                    $read_n = $stream.Read($buffer, 0, $buffer.Length)
                    if ($read_n -le 0) {
                        break
                    }
                    [void]$builder.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $read_n))
                }
                else {
                    Start-Sleep -Milliseconds 10
                }
            }

            foreach ($line in ($builder.ToString() -split "`n")) {
                $line = $line.Trim()
                if (-not $line) {
                    continue
                }
                $response = handle_control_request -request_line $line
                $response_bytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $response -Compress -Depth 8) + "`n")
                $stream.Write($response_bytes, 0, $response_bytes.Length)
            }
        }
        catch {
            write_runtime_log -message ('AI 控制通道处理异常: {0}' -f $_.Exception.Message)
        }
        finally {
            try {
                $client.Close()
            }
            catch {
            }
        }
    }
    catch {
    }
}

function start_headless_session {
    param(
        [string]$PortName,
        [int]$BaudRate,
        [string]$ProtocolName,
        [double]$DurationSeconds,
        [string]$OutFile,
        [string]$ErrorRegex,
        [string]$TriggerFile,
        [string]$ExpectFile,
        [string]$RecordPath,
        [string]$ReplayPath,
        [bool]$EnableDtr,
        [bool]$EnableRts,
        [string]$FlashCommand,
        [int]$FlashTimeoutMs = 300000,
        [string]$ResetMode,
        [string]$ResetCommand
    )

    if ($DurationSeconds -le 0) {
        $DurationSeconds = 10
    }
    if ($ProtocolName -notin @('FireWater', 'JustFloat', 'RawData')) {
        throw ('协议无效: ' + $ProtocolName)
    }
    if ($ReplayPath -and $PortName) {
        throw '-ReplayFile 与 -Port 不能同时使用。'
    }
    if ($ReplayPath -and ($FlashCommand -or $ResetMode)) {
        throw '-ReplayFile 不能与 -FlashCommand/-ResetMode 同时使用。'
    }
    if (-not $ReplayPath -and -not $PortName) {
        $available_ports = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object)
        if ($available_ports.Count -eq 0) {
            throw '未找到可用串口，请用 -Port 指定。'
        }
        $PortName = $available_ports[0]
        Write-Host ('未指定 -Port，自动选择: ' + $PortName)
    }

    $log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'
    if (-not (Test-Path -LiteralPath $log_dir)) {
        New-Item -ItemType Directory -Path $log_dir | Out-Null
    }
    if (-not $OutFile) {
        if ($ReplayPath) {
            $OutFile = Join-Path $log_dir ('headless_replay_{0}.jsonl' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
        }
        else {
            $OutFile = Join-Path $log_dir ('headless_{0}.jsonl' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
        }
    }

    $script:wave_enabled = $true
    $script:wave_protocol = $ProtocolName
    reset_wave_buffers
    reset_wave_decoder

    $trigger_engine = $null
    if ($TriggerFile) {
        $trigger_engine = new_trigger_engine -config_file $TriggerFile -events_dir (Join-Path $log_dir 'events')
    }

    $writer = [System.IO.StreamWriter]::new($OutFile, $false, [System.Text.UTF8Encoding]::new($false))
    $writer.AutoFlush = $true
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $now_iso = { (Get-Date).ToString('o') }

    $state = @{ rx_bytes = [long]0; lines = [long]0 }
    $error_hits = New-Object 'System.Collections.Generic.List[object]'
    $error_regex = $null
    if ($ErrorRegex) {
        $error_regex = [System.Text.RegularExpressions.Regex]::new($ErrorRegex, [System.Text.RegularExpressions.RegexOptions]'Compiled,IgnoreCase')
    }
    $text_decoder = [System.Text.UTF8Encoding]::new($false, $false).GetDecoder()
    $text_buffer = ''

    $emit_jsonl = {
        param($obj)
        $writer.WriteLine((ConvertTo-Json $obj -Compress -Depth 6))
    }

    $process_chunk = {
        param([byte[]]$bytes, [long]$ms, [bool]$write_data)

        if (-not $bytes -or $bytes.Length -le 0) {
            return
        }
        if ($write_data) {
            & $emit_jsonl @{ t = (& $now_iso); ms = $ms; dir = 'DATA'; b64 = [Convert]::ToBase64String($bytes); n = $bytes.Length }
        }
        $state.rx_bytes += $bytes.Length

        $before = $script:wave_frame_count
        try_consume_wave_bytes -bytes $bytes
        $after = $script:wave_frame_count

        if ($script:wave_protocol -eq 'FireWater') {
            $char_count = $text_decoder.GetCharCount($bytes, 0, $bytes.Length)
            if ($char_count -gt 0) {
                $chars = New-Object char[] $char_count
                [void]$text_decoder.GetChars($bytes, 0, $bytes.Length, $chars, 0)
                $text_buffer += [string]::new($chars)
            }
            $normalized = $text_buffer -replace "`r`n", "`n" -replace "`r", "`n"
            $parts = [System.Text.RegularExpressions.Regex]::Split($normalized, "`n")
            $text_buffer = $parts[$parts.Count - 1]
            for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
                $line = $parts[$index]
                $state.lines++
                $row = @{ t = (& $now_iso); ms = $ms; dir = 'RX'; text = $line }
                $frame = try_parse_wave_frame -line $line
                if ($null -ne $frame) {
                    $row.ch = @($frame)
                }
                if ($error_regex -and $error_regex.IsMatch($line)) {
                    $row.hit = $true
                    [void]$error_hits.Add(@{ ms = $ms; text = $line })
                }
                & $emit_jsonl $row

                $decoded_fault = test_fault_dump_line -text $line
                if ($decoded_fault) {
                    & $emit_jsonl @{ t = (& $now_iso); ms = $ms; dir = 'FAULT'; text = $line; decoded = $decoded_fault.decoded }
                }

                if ($trigger_engine) {
                    $events = @(feed_trigger_line -engine $trigger_engine -line $line -ms $ms)
                    foreach ($event_item in $events) {
                        & $emit_jsonl @{ t = (& $now_iso); ms = $ms; dir = 'EVT'; name = $event_item.name; text = $event_item.text; file = $event_item.file }
                    }
                }
            }
            if ($text_buffer.Length -ge 8192) {
                $text_buffer = ''
            }
        }
        elseif ($after -gt $before) {
            if ($script:wave_protocol -eq 'RawData') {
                $samples = @()
                for ($idx = $before; $idx -lt $after; $idx++) {
                    $samples += $script:wave_ch_values[0][$idx]
                }
                & $emit_jsonl @{ t = (& $now_iso); ms = $ms; dir = 'RX'; samples = $samples; count = $samples.Count }
            }
            else {
                for ($idx = $before; $idx -lt $after; $idx++) {
                    $ch_row = @()
                    for ($c = 0; $c -lt $script:wave_ch_values.Count; $c++) {
                        $ch_row += $script:wave_ch_values[$c][$idx]
                    }
                    & $emit_jsonl @{ t = (& $now_iso); ms = $ms; dir = 'RX'; ch = $ch_row }
                }
            }
        }
    }

    $serial = $null
    $flash_result = $null
    $reset_result = $null
    if (-not $ReplayPath) {
        # 编排阶段：先复位 / 烧录（串口未打开），成功后再打开串口捕获
        if ($ResetMode) {
            if ($ResetMode -notin @('rts', 'dtr', 'command')) {
                throw ('ResetMode 无效: ' + $ResetMode)
            }
            if ($ResetMode -eq 'command') {
                if (-not $ResetCommand) {
                    throw 'ResetMode=command 需要 -ResetCommand'
                }
                Write-Host ('复位(命令): ' + $ResetCommand)
                $reset_result = invoke_stream_command -command $ResetCommand -timeout_ms 120000
                if ($reset_result.timed_out -or $reset_result.exit_code -ne 0) {
                    & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'RESET'; mode = 'command'; exit_code = $reset_result.exit_code; timed_out = $reset_result.timed_out }
                    $writer.Dispose()
                    Write-Host ('结果: 复位命令失败 → 退出码 4')
                    return 4
                }
                & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'RESET'; mode = 'command'; exit_code = 0 }
            }
            else {
                $pulse_port = New-Object System.IO.Ports.SerialPort
                $pulse_port.PortName = $PortName
                $pulse_port.BaudRate = $BaudRate
                $pulse_port.ReadTimeout = 200
                $pulse_port.WriteTimeout = 500
                $pulse_port.Open()
                try {
                    if ($ResetMode -eq 'dtr') {
                        $pulse_port.DtrEnable = $true
                        Start-Sleep -Milliseconds 150
                        $pulse_port.DtrEnable = $false
                    }
                    else {
                        $pulse_port.RtsEnable = $true
                        Start-Sleep -Milliseconds 150
                        $pulse_port.RtsEnable = $false
                    }
                }
                finally {
                    $pulse_port.Close()
                    $pulse_port.Dispose()
                }
                & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'RESET'; mode = $ResetMode }
            }
            Start-Sleep -Milliseconds 60
        }

        if ($FlashCommand) {
            Write-Host ('烧录: ' + $FlashCommand)
            $flash_result = invoke_stream_command -command $FlashCommand -timeout_ms $FlashTimeoutMs
            foreach ($line in $flash_result.lines) {
                & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'FLASH'; text = $line }
            }
            & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'FLASH_RESULT'; exit_code = $flash_result.exit_code; timed_out = $flash_result.timed_out }
            if ($flash_result.timed_out -or $flash_result.exit_code -ne 0) {
                $writer.Dispose()
                if ($flash_result.timed_out) {
                    Write-Host '结果: 烧录超时 → 退出码 5'
                }
                else {
                    Write-Host ('结果: 烧录失败 exit={0} → 退出码 5' -f $flash_result.exit_code)
                }
                return 5
            }
            Write-Host '烧录成功，开始捕获...'
        }
    }

    if (-not $ReplayPath) {
        $serial = New-Object System.IO.Ports.SerialPort
        $serial.PortName = $PortName
        $serial.BaudRate = $BaudRate
        $serial.DataBits = 8
        $serial.StopBits = [System.IO.Ports.StopBits]::One
        $serial.Parity = [System.IO.Ports.Parity]::None
        $serial.Handshake = [System.IO.Ports.Handshake]::None
        $serial.DtrEnable = $EnableDtr
        $serial.RtsEnable = $EnableRts
        $serial.ReadTimeout = 100
        $serial.WriteTimeout = 1000
        $serial.ReadBufferSize = 1048576
        $serial.Open()
        Write-Host ('已连接 {0} @ {1} | 协议 {2} | 捕获 {3}s → {4}' -f $PortName, $BaudRate, $ProtocolName, $DurationSeconds, $OutFile)
    }
    else {
        Write-Host ('回放 {0} → {1}' -f $ReplayPath, $OutFile)
    }
    & $emit_jsonl @{ t = (& $now_iso); ms = 0; dir = 'META'; port = $PortName; baud = $BaudRate; protocol = $ProtocolName; replay = [bool]$ReplayPath }

    # 期望测试阶段（先于捕获执行，结果写入同一 JSONL）
    $test_summary = $null
    if ($ExpectFile) {
        if (-not $serial) {
            throw '回放模式不支持期望测试（-ExpectFile 需要真实串口）。'
        }
        $steps_config = Get-Content -LiteralPath $ExpectFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $headless_send = {
            param($text, [bool]$as_hex, $newline_mode)
            $tx_ms = $stopwatch.ElapsedMilliseconds
            if ($as_hex) {
                $payload_bytes = try_parse_hex_text -text $text
                $newline_bytes = get_newline_bytes_for_mode -mode $newline_mode
                if ($newline_bytes.Count -gt 0) {
                    # [object[]] cast 对标量包一层、对数组扁平化，避免 byte[]+数组 的 op_Addition
                    $payload_bytes = @([object[]]$payload_bytes + [object[]]$newline_bytes)
                }
                if ($payload_bytes.Count -gt 0) {
                    $serial.Write([byte[]]$payload_bytes, 0, $payload_bytes.Count)
                }
            }
            else {
                $payload_text = [string]$text
                switch ($newline_mode) {
                    'CR' { $payload_text += "`r" }
                    'LF' { $payload_text += "`n" }
                    'CRLF' { $payload_text += "`r`n" }
                }
                if ($payload_text.Length -gt 0) {
                    $serial.Write($payload_text)
                }
            }
            & $emit_jsonl @{ t = (& $now_iso); ms = $tx_ms; dir = 'TX'; text = [string]$text; hex = [bool]$as_hex }
        }
        $expect_read_state = @{ decoder = [System.Text.UTF8Encoding]::new($false, $false).GetDecoder(); sb = New-Object System.Text.StringBuilder }
        $headless_read = {
            param([int]$timeout_ms)
            read_serial_text_window -serial $serial -timeout_ms $timeout_ms -state $expect_read_state
        }
        $test_summary = invoke_expect_steps -steps @($steps_config) -send_func $headless_send -read_func $headless_read
        foreach ($step_result in $test_summary.steps) {
            & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'TEST'; name = $step_result.name; send = $step_result.send; expect = $step_result.expect; pass = $step_result.pass; elapsed_ms = $step_result.elapsed_ms; matched = $step_result.matched }
        }
        & $emit_jsonl @{ t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'TEST_SUMMARY'; total = $test_summary.total; fail = $test_summary.fail; all_pass = $test_summary.all_pass }
    }

    if ($ReplayPath) {
        $records = Get-Content -LiteralPath $ReplayPath -Encoding UTF8
        foreach ($record_line in $records) {
            if (-not $record_line) {
                continue
            }
            try {
                $record = $record_line | ConvertFrom-Json
            }
            catch {
                continue
            }
            if ([string](get_json_prop $record 'dir') -ne 'DATA') {
                continue
            }
            $b64 = [string](get_json_prop $record 'b64')
            $replay_ms = [long](get_json_prop $record 'ms' 0)
            if ($b64) {
                & $process_chunk -bytes ([Convert]::FromBase64String($b64)) -ms $replay_ms -write_data $false
            }
        }
    }
    else {
        $deadline = $stopwatch.ElapsedMilliseconds + [long]($DurationSeconds * 1000)
        $buffer = New-Object byte[] 32768
        while ($stopwatch.ElapsedMilliseconds -lt $deadline) {
            if ($serial.BytesToRead -gt 0) {
                $read_size = [Math]::Min($serial.BytesToRead, $buffer.Length)
                $read_n = $serial.Read($buffer, 0, $read_size)
                if ($read_n -gt 0) {
                    $chunk = New-Object byte[] $read_n
                    [Array]::Copy($buffer, $chunk, $read_n)
                    & $process_chunk -bytes $chunk -ms $stopwatch.ElapsedMilliseconds -write_data ([bool]$RecordPath)
                }
            }
            else {
                Start-Sleep -Milliseconds 5
            }
        }
    }

    $test_total_value = 0
    $test_fail_value = 0
    if ($test_summary) {
        $test_total_value = $test_summary.total
        $test_fail_value = $test_summary.fail
    }
    $trigger_hits_value = 0
    if ($trigger_engine) {
        $trigger_hits_value = $trigger_engine.hits
    }
    & $emit_jsonl @{
        t = (& $now_iso); ms = $stopwatch.ElapsedMilliseconds; dir = 'SUMMARY'
        rx_bytes = $state.rx_bytes; lines = $state.lines
        frames = $script:wave_frame_total; buffered = $script:wave_frame_count; channels = $script:wave_ch_values.Count
        error_hits = $error_hits.Count
        test_total = $test_total_value; test_fail = $test_fail_value
        trigger_hits = $trigger_hits_value
        out = $OutFile
    }

    if ($serial) {
        try {
            if ($serial.IsOpen) {
                $serial.Close()
            }
            $serial.Dispose()
        }
        catch {
        }
    }
    $writer.Dispose()

    if ($error_hits.Count -gt 0) {
        Write-Host ('结果: 命中错误模式 {0} 次 → 退出码 2' -f $error_hits.Count)
        return 2
    }
    if ($test_summary -and -not $test_summary.all_pass) {
        Write-Host ('结果: 期望测试失败 {0}/{1} → 退出码 3' -f $test_summary.fail, $test_summary.total)
        return 3
    }
    Write-Host ('结果: 正常 → 退出码 0（RX {0} 字节 / {1} 行 / {2} 帧）' -f $state.rx_bytes, $state.lines, $script:wave_frame_total)
    return 0
}

function invoke_reset_pulse {
    param([string]$mode)

    $created = $false
    $port = $script:serial_port
    if (-not $port -or -not $port.IsOpen) {
        $port_name = get_port_name_from_display -text $combobox_port.Text
        if ([string]::IsNullOrWhiteSpace($port_name)) {
            throw '请先选择串口。'
        }
        $pulse_baud = 0
        if (-not [int]::TryParse($combobox_baud.Text.Trim(), [ref]$pulse_baud)) {
            throw '波特率不是有效整数。'
        }
        $port = New-Object System.IO.Ports.SerialPort
        $port.PortName = $port_name
        $port.BaudRate = $pulse_baud
        $port.ReadTimeout = 200
        $port.WriteTimeout = 500
        $port.Open()
        $created = $true
    }

    try {
        # 脉冲极性取决于板载自动复位电路（DTR/RTS→RESET 晶体管），两种模式都提供
        if ($mode -eq 'dtr') {
            $port.DtrEnable = $true
            Start-Sleep -Milliseconds 150
            $port.DtrEnable = $false
        }
        else {
            $port.RtsEnable = $true
            Start-Sleep -Milliseconds 150
            $port.RtsEnable = $false
        }
        Start-Sleep -Milliseconds 60
    }
    finally {
        if ($created) {
            try {
                $port.Close()
                $port.Dispose()
            }
            catch {
            }
        }
    }
}

function invoke_stream_command {
    param(
        [string]$command,
        [int]$timeout_ms = 300000,
        [switch]$PumpUi
    )

    if ([string]::IsNullOrWhiteSpace($command)) {
        throw '命令为空。'
    }

    $tmp_out = [System.IO.Path]::GetTempFileName()
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = ('/c ' + $command + ' > "' + $tmp_out + '" 2>&1')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($psi)

    $timed_out = $false
    $deadline = [datetime]::Now.AddMilliseconds($timeout_ms)
    while (-not $process.HasExited) {
        if ([datetime]::Now -gt $deadline) {
            try {
                $process.Kill()
            }
            catch {
            }
            $timed_out = $true
            break
        }
        if ($PumpUi) {
            [System.Windows.Forms.Application]::DoEvents()
        }
        Start-Sleep -Milliseconds 200
    }

    $exit_code = 0
    if ($timed_out) {
        $exit_code = -1
    }
    else {
        $exit_code = $process.ExitCode
    }
    try {
        $process.Dispose()
    }
    catch {
    }

    $lines = @()
    if (Test-Path -LiteralPath $tmp_out) {
        $raw_lines = [System.IO.File]::ReadAllLines($tmp_out, [System.Text.Encoding]::Default)
        foreach ($line in $raw_lines) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $lines += $line
            }
        }
        Remove-Item -LiteralPath $tmp_out -Force -ErrorAction SilentlyContinue
    }

    return [pscustomobject]@{
        exit_code = $exit_code
        timed_out = $timed_out
        lines = $lines
    }
}

