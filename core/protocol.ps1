# core\protocol.ps1 — 波形协议解析与缓冲（纯逻辑，不触碰控件）
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function try_parse_wave_frame {
    param([string]$line)

    if ([string]::IsNullOrWhiteSpace($line)) {
        return $null
    }

    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.Length -gt 4096) {
        return $null
    }

    # FireWater 命令通道（如 $H:1.0）不是数据行
    if ($trimmed.StartsWith('$')) {
        return $null
    }

    $fields = $trimmed.Split(',')
    if ($fields.Count -lt 1 -or $fields.Count -gt 32) {
        return $null
    }

    $values = [double[]]::new($fields.Count)
    for ($i = 0; $i -lt $fields.Count; $i++) {
        $field = $fields[$i].Trim()
        if ($field.Length -eq 0 -or -not $script:wave_number_regex.IsMatch($field)) {
            return $null
        }

        $value = 0.0
        if (-not [double]::TryParse($field, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$value)) {
            return $null
        }
        $values[$i] = $value
    }

    return ,$values
}

function append_wave_frame {
    param([double[]]$values)

    # 暂停 = 波形缓冲真正冻结（便于频谱仪/滤波/Marker 在同一快照上分析）；
    # RX 显示、自动日志、录制不受影响
    if ($script:wave_paused) {
        return
    }

    if (-not $values -or $values.Count -eq 0) {
        return
    }

    $count = $values.Count
    while ($script:wave_ch_values.Count -lt $count) {
        # 新通道用 0 补齐历史帧，保持各通道缓冲长度一致（对齐）
        $new_list = New-Object 'System.Collections.Generic.List[double]'
        for ($j = 0; $j -lt $script:wave_frame_count; $j++) {
            [void]$new_list.Add(0.0)
        }
        [void]$script:wave_ch_values.Add($new_list)
        [void]$script:wave_channel_last.Add(0.0)
        ensure_wave_channel_checks
    }

    # 短帧用各通道最近值补齐，保证每条通道曲线连续
    if ($count -lt $script:wave_ch_values.Count) {
        $padded = New-Object double[] $script:wave_ch_values.Count
        for ($i = 0; $i -lt $count; $i++) {
            $padded[$i] = $values[$i]
        }
        for ($i = $count; $i -lt $padded.Count; $i++) {
            $padded[$i] = $script:wave_channel_last[$i]
        }
        $values = $padded
    }

    for ($i = 0; $i -lt $values.Count; $i++) {
        [void]$script:wave_ch_values[$i].Add($values[$i])
        $script:wave_channel_last[$i] = $values[$i]
    }

    $script:wave_frame_count++
    $script:wave_frame_total++
    $script:wave_fps_count++

    if ($script:wave_frame_count -gt $script:wave_max_frames) {
        foreach ($list in $script:wave_ch_values) {
            $list.RemoveRange(0, 10000)
        }
        $script:wave_frame_count -= 10000
        if ($script:wave_view_start -ge 0) {
            $script:wave_view_start -= 10000
            if ($script:wave_view_start -lt 0) { $script:wave_view_start = -1 }
        }
    }
}

function reset_wave_decoder {
    $script:wave_rx_decoder = [System.Text.UTF8Encoding]::new($false, $false).GetDecoder()
    $script:wave_line_buffer = ''
}

function note_wave_buffer_overflow {
    # 缓冲积压爆掉（失步/超长丢弃）时给一条 SYS 提示，5 秒节流防刷屏。
    # 典型诱因：设备发的不是当前选的协议格式，或波特率不对（解码全是乱码凑不出行）。
    param([string]$detail)

    $now = [datetime]::Now
    if (($now - $script:wave_overflow_note_last).TotalMilliseconds -lt 5000) {
        return
    }
    $script:wave_overflow_note_last = $now
    add_message_entry -direction 'SYS' -text ('[积压] {0}。收到的内容可能不是当前协议格式，或波特率不匹配。' -f $detail)
}

function reset_wave_buffers {
    $script:wave_ch_values = New-Object 'System.Collections.Generic.List[object]'
    $script:wave_channel_last = New-Object 'System.Collections.Generic.List[double]'
    $script:wave_channel_checks = New-Object 'System.Collections.Generic.List[object]'
    $script:wave_channel_names = New-Object 'System.Collections.Generic.List[string]'
    $script:wave_jf_buffer.Clear()
    $script:wave_frame_count = 0
    $script:wave_frame_total = [long]0
    $script:wave_fail_total = [long]0
    $script:wave_last_painted_frame = 0
    $script:wave_fps_count = 0
    $script:wave_hint_last_frames = 0
    $script:wave_view_start = -1
    $script:wave_filter_cache = @{}
    # XY 余晖累积层一并清空（位图由 wave_form.ps1 管理，未开窗时为 $null）
    $script:wave_xy_last_abs = [long]0
    dispose_wave_xy_bitmaps
    if ($script:wave_channel_flow -and -not $script:wave_channel_flow.IsDisposed) {
        $remove_list = @()
        foreach ($control in $script:wave_channel_flow.Controls) {
            if ($control -ne $script:wave_channel_header) {
                $remove_list += $control
            }
        }
        foreach ($control in $remove_list) {
            [void]$script:wave_channel_flow.Controls.Remove($control)
        }
        # XY 控制区刚被移出面板。引用必须清掉：不清的话 ensure_wave_xy_section
        # 会走"已存在"分支对一个没有父容器的 section 调 SetChildIndex 直接崩
        # （点「清空」/切协议后再来数据就会触发）。下次 ensure 会整体重建。
        if ($script:wave_xy_section) {
            try { $script:wave_xy_section.Dispose() } catch {}
            $script:wave_xy_section = $null
        }
    }
    if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
        $script:wave_panel.Invalidate()
    }
}

function consume_wave_firewater_bytes {
    param([byte[]]$bytes)

    if (-not $script:wave_rx_decoder) {
        reset_wave_decoder
    }

    $char_count = $script:wave_rx_decoder.GetCharCount($bytes, 0, $bytes.Length)
    if ($char_count -le 0) {
        return
    }
    $chars = New-Object char[] $char_count
    [void]$script:wave_rx_decoder.GetChars($bytes, 0, $bytes.Length, $chars, 0)
    $script:wave_line_buffer += [string]::new($chars)

    $normalized = $script:wave_line_buffer -replace "`r`n", "`n" -replace "`r", "`n"
    $parts = [System.Text.RegularExpressions.Regex]::Split($normalized, "`n")
    if ($parts.Count -le 1) {
        if ($script:wave_line_buffer.Length -ge 8192) {
            $script:wave_line_buffer = ''
            $script:wave_fail_total++
            note_wave_buffer_overflow -detail 'FireWater 行缓冲超长已丢弃'
        }
        return
    }

    for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
        ingest_wave_firewater_line -line $parts[$index]
    }
    $script:wave_line_buffer = $parts[$parts.Count - 1]
    if ($script:wave_line_buffer.Length -ge 8192) {
        $script:wave_line_buffer = ''
        note_wave_buffer_overflow -detail 'FireWater 行缓冲超长已丢弃'
    }
}

function ingest_wave_firewater_line {
    param([string]$line)

    $frame = try_parse_wave_frame -line $line
    if ($null -eq $frame -and $line.IndexOf([char]27) -ge 0) {
        # 含 ANSI 控制序列的行，清洗后再试一次
        $cleaned = sanitize_terminal_text -text $line
        $frame = try_parse_wave_frame -line $cleaned
    }

    if ($null -eq $frame) {
        if ($line.Contains(',')) {
            $script:wave_fail_total++
        }
        return
    }

    append_wave_frame -values $frame
}

function consume_wave_justfloat_bytes {
    param([byte[]]$bytes)

    # JustFloat: N×float32(小端) + 帧尾 00 00 80 7F
    $script:wave_jf_buffer.AddRange($bytes)

    while ($script:wave_jf_buffer.Count -ge 4) {
        $count = $script:wave_jf_buffer.Count
        $tail_index = -1
        for ($i = 0; $i -le ($count - 4); $i++) {
            if ($script:wave_jf_buffer[$i] -eq 0 -and $script:wave_jf_buffer[$i + 1] -eq 0 -and
                $script:wave_jf_buffer[$i + 2] -eq 0x80 -and $script:wave_jf_buffer[$i + 3] -eq 0x7F) {
                $tail_index = $i
                break
            }
        }

        if ($tail_index -lt 0) {
            # 未找到帧尾：保留全部已到数据等待后续字节（超长则判定失步，丢弃重来）
            if ($count -gt 65536) {
                $script:wave_jf_buffer.Clear()
                $script:wave_fail_total++
                note_wave_buffer_overflow -detail 'JustFloat 找不到帧尾，缓冲失步已丢弃重来'
            }
            return
        }

        if ($tail_index -gt 0 -and (($tail_index % 4) -eq 0)) {
            $payload = $script:wave_jf_buffer.GetRange(0, $tail_index).ToArray()
            $values = New-Object double[] ($tail_index / 4)
            for ($k = 0; $k -lt $values.Count; $k++) {
                $values[$k] = [BitConverter]::ToSingle($payload, $k * 4)
            }
            append_wave_frame -values $values
        }
        else {
            # 载荷长度不是 4 的倍数（帧不同步或脏数据）
            $script:wave_fail_total++
        }
        $script:wave_jf_buffer.RemoveRange(0, $tail_index + 4)
    }
}

function consume_wave_rawdata_bytes {
    param([byte[]]$bytes)

    # RawData: 每个字节 (0~255) 作为一个采样点，单通道连续流
    if (-not $bytes -or $bytes.Length -eq 0) {
        return
    }

    # 暂停 = 波形缓冲真正冻结（与 append_wave_frame 同语义）
    if ($script:wave_paused) {
        return
    }

    if ($script:wave_ch_values.Count -eq 0) {
        [void]$script:wave_ch_values.Add((New-Object 'System.Collections.Generic.List[double]'))
        [void]$script:wave_channel_last.Add(0.0)
        ensure_wave_channel_checks
    }

    $list = $script:wave_ch_values[0]
    foreach ($byte_value in $bytes) {
        [void]$list.Add([double]$byte_value)
    }
    $script:wave_channel_last[0] = [double]$bytes[$bytes.Length - 1]

    $script:wave_frame_count += $bytes.Length
    $script:wave_frame_total += $bytes.Length
    $script:wave_fps_count += $bytes.Length

    if ($script:wave_frame_count -gt $script:wave_max_frames) {
        $remove_count = $script:wave_frame_count - $script:wave_max_frames
        foreach ($list_inner in $script:wave_ch_values) {
            $list_inner.RemoveRange(0, $remove_count)
        }
        $script:wave_frame_count -= $remove_count
        if ($script:wave_view_start -ge 0) {
            $script:wave_view_start -= $remove_count
            if ($script:wave_view_start -lt 0) { $script:wave_view_start = -1 }
        }
    }
}

function try_consume_wave_bytes {
    param([byte[]]$bytes)

    if (-not $script:wave_enabled -or -not $bytes -or $bytes.Length -eq 0) {
        return
    }

    switch ($script:wave_protocol) {
        'JustFloat' {
            consume_wave_justfloat_bytes -bytes $bytes
        }
        'RawData' {
            consume_wave_rawdata_bytes -bytes $bytes
        }
        default {
            consume_wave_firewater_bytes -bytes $bytes
        }
    }
}

function get_wave_channel_display_name {
    param([int]$index)

    if ($index -ge 0 -and $index -lt $script:wave_channel_names.Count -and -not [string]::IsNullOrWhiteSpace($script:wave_channel_names[$index])) {
        return [string]$script:wave_channel_names[$index]
    }
    return 'CH{0}' -f $index
}

function set_wave_channel_name {
    param([int]$index, [string]$name)

    if ($index -lt 0 -or $index -ge $script:wave_ch_values.Count) {
        return
    }

    $clean = ([string]$name).Trim() -replace '[,，\r\n]', ''
    if ([string]::IsNullOrWhiteSpace($clean)) {
        return
    }

    while ($script:wave_channel_names.Count -le $index) {
        [void]$script:wave_channel_names.Add('')
    }
    $script:wave_channel_names[$index] = $clean

    if ($index -lt $script:wave_channel_checks.Count) {
        $script:wave_channel_checks[$index].Text = $clean
    }

    save_gui_setting -key 'channel_names' -value (($script:wave_channel_names | ForEach-Object { $_ }) -join ',')

    if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
        $script:wave_panel.Invalidate()
    }
}

function get_nice_step {
    param(
        [double]$range,
        [int]$target_lines
    )

    if ($range -le 0 -or [double]::IsInfinity($range) -or [double]::IsNaN($range)) {
        return 1.0
    }

    $raw = $range / [Math]::Max(1, $target_lines)
    if ($raw -le 0) {
        return 1.0
    }

    $magnitude = [Math]::Pow(10, [Math]::Floor([Math]::Log10($raw)))
    $normalized = $raw / $magnitude
    if ($normalized -ge 5) { return 5 * $magnitude }
    if ($normalized -ge 2) { return 2 * $magnitude }
    return $magnitude
}

function get_wave_view_range {
    # 计算当前可视帧窗口。view_start < 0 = 实时跟随（显示最后 width 帧）；
    # >= 0 = 历史浏览（[view_start, view_start+width]）。纯逻辑可单测。
    param([int]$total)

    $width = [int]$script:wave_window_frames
    if ($width -lt 50) { $width = 50 }
    if ($total -le 0) {
        return [pscustomobject]@{ Start = 0; End = 0; Follow = $true }
    }
    if ($width -ge $total) {
        $script:wave_view_start = -1
        return [pscustomobject]@{ Start = 0; End = $total; Follow = $true }
    }
    if ($script:wave_view_start -lt 0) {
        return [pscustomobject]@{ Start = ($total - $width); End = $total; Follow = $true }
    }

    $start = $script:wave_view_start
    if ($start -gt ($total - $width)) {
        # 拖回/裁剪到最右端 → 恢复实时跟随
        $script:wave_view_start = -1
        return [pscustomobject]@{ Start = ($total - $width); End = $total; Follow = $true }
    }
    return [pscustomobject]@{ Start = $start; End = ($start + $width); Follow = $false }
}

function set_wave_view_pan {
    # 平移可视窗口（拖动横轴/曲线区）。delta_frames > 0 = 看更新数据，< 0 = 看更旧数据。
    param([int]$delta_frames, [int]$total)

    if ($delta_frames -eq 0 -or $total -le 1) {
        return
    }

    $width = [int]$script:wave_window_frames
    if ($width -ge $total) {
        $script:wave_view_start = -1
        return
    }

    $view = get_wave_view_range -total $total
    $new_start = [int][Math]::Round($view.Start) + $delta_frames
    if ($new_start -lt 0) { $new_start = 0 }
    if ($new_start -ge ($total - $width)) {
        # 到最右端 → 恢复实时跟随
        $script:wave_view_start = -1
        return
    }
    $script:wave_view_start = $new_start
}

function set_wave_view_window {
    # 滑块绝对定位窗口：直接设置窗口起止（鼠标指哪窗口到哪）。
    # anchor = 被拖的点是哪侧：start（左端跟随鼠标、end 钉住）/ end（反之）/ mid（整体平移）。
    # 宽度下限 50 帧；end 触到缓冲末尾 → 实时跟随。
    param([int]$new_start, [int]$new_end, [int]$total, [string]$anchor = 'mid')

    if ($total -le 1) {
        return
    }

    if ($new_end -gt $total) { $new_end = $total }
    if ($new_start -lt 0) { $new_start = 0 }

    if (($new_end - $new_start) -lt 50) {
        # 拖过了头：被拖侧钳位到另一侧 ±50，锚点侧不动
        if ($anchor -eq 'start') {
            $new_start = $new_end - 50
        }
        elseif ($anchor -eq 'end') {
            $new_end = $new_start + 50
        }
        else {
            $center = ($new_start + $new_end) / 2.0
            $new_start = [int][Math]::Round($center - 25)
            $new_end = $new_start + 50
        }
        if ($new_start -lt 0) {
            $new_start = 0
            $new_end = 50
        }
        if ($new_end -gt $total) {
            $new_end = $total
            $new_start = $total - 50
            if ($new_start -lt 0) { $new_start = 0 }
        }
    }

    $width = $new_end - $new_start
    $script:wave_window_frames = $width
    if ($script:wcombo_window) { $script:wcombo_window.Text = [string]$width }

    if ($new_end -ge $total) {
        # 右端贴住末尾 → 实时跟随
        $script:wave_view_start = -1
        return
    }

    $script:wave_view_start = $new_start
}

function set_wave_view_width {
    # 修改窗口宽度；历史浏览时保持窗口中心不变，实时跟随模式不受影响
    param([int]$new_width, [int]$total)

    if ($new_width -lt 50) { $new_width = 50 }
    if ($new_width -gt 200000) { $new_width = 200000 }
    if ($new_width -eq [int]$script:wave_window_frames) {
        return
    }

    $old_width = [int]$script:wave_window_frames
    $script:wave_window_frames = $new_width
    if ($script:wcombo_window) { $script:wcombo_window.Text = [string]$new_width }

    if ($script:wave_view_start -ge 0 -and $total -gt 1 -and $old_width -gt 0) {
        $center = $script:wave_view_start + $old_width / 2.0
        $new_start = [int][Math]::Round($center - $new_width / 2.0)
        if ($new_start -lt 0) { $new_start = 0 }
        if ($new_width -ge $total) {
            $script:wave_view_start = -1
        }
        elseif ($new_start -gt ($total - $new_width)) {
            $script:wave_view_start = $total - $new_width
        }
        else {
            $script:wave_view_start = $new_start
        }
    }
}

function get_wave_slider_hit {
    # 判定滑块上按中的点：'start'（左红）/ 'mid'（中绿）/ 'end'（右紫）/ 'none'
    param([int]$x, [int]$width, [int]$total)

    if ($total -lt 2) {
        return 'none'
    }

    $metrics = get_wave_slider_metrics -width $width
    $view = get_wave_view_range -total $total
    $x_start = get_wave_slider_frame_x -frame $view.Start -total $total -track_left $metrics.Left -track_span $metrics.Span
    $x_end = get_wave_slider_frame_x -frame ($view.End - 1) -total $total -track_left $metrics.Left -track_span $metrics.Span
    $x_mid = ($x_start + $x_end) / 2.0

    $d_start = [Math]::Abs($x - $x_start)
    $d_mid = [Math]::Abs($x - $x_mid)
    $d_end = [Math]::Abs($x - $x_end)
    $threshold = 14

    $best = 'none'
    $best_d = $threshold
    if ($d_start -lt $best_d) { $best = 'start'; $best_d = $d_start }
    if ($d_mid -lt $best_d) { $best = 'mid'; $best_d = $d_mid }
    if ($d_end -lt $best_d) { $best = 'end'; $best_d = $d_end }
    return $best
}

function get_wave_filter_key {
    # 滤波缓存键：数据版本（帧数，trim 也会变）+ 模式 + 参数
    return ('{0}|{1}|{2}' -f $script:wave_frame_count, $script:wave_filter_mode, $script:wave_filter_param)
}

function get_wave_view_data {
    # 取通道在视图区间的数据（滤波为非破坏性视图：原始缓冲不动，
    # 结果按当前参数对整段缓冲重算并缓存 → 切换滤波立即对旧数据生效）
    param([int]$channel, [int]$start, [int]$end)

    $channel_list = $script:wave_ch_values[$channel]
    if ($null -eq $channel_list) {
        return @{ Data = [double[]]::new(0); Offset = 0 }
    }

    $key = get_wave_filter_key
    $cache = $script:wave_filter_cache[$channel]
    if (-not $cache -or $cache.Ver -ne $key) {
        $raw = [double[]]::new($channel_list.Count)
        for ($i = 0; $i -lt $channel_list.Count; $i++) {
            $raw[$i] = [double]$channel_list[$i]
        }

        $filtered = $raw
        switch ($script:wave_filter_mode) {
            '滑动平均' {
                $win = [Math]::Max(2, [int]$script:wave_filter_param)
                $filtered = [WaveDsp]::MovingAverage($raw, $win)
            }
            '零相位低通' {
                # 强度 N → alpha = 1/N：N 越大越平滑
                $n = [Math]::Max(2, [int]$script:wave_filter_param)
                $filtered = [WaveDsp]::ZeroPhaseLowPass($raw, 1.0 / $n)
            }
            default {
                $filtered = $raw
            }
        }

        $cache = @{ Ver = $key; Data = [double[]]$filtered }
        $script:wave_filter_cache[$channel] = $cache
    }

    $safe_start = [Math]::Max(0, [Math]::Min($start, $cache.Data.Length))
    $safe_end = [Math]::Max($safe_start, [Math]::Min($end, $cache.Data.Length))
    return @{ Data = $cache.Data; Offset = $safe_start; Length = ($safe_end - $safe_start) }
}

function Get-WavePluginSnapshot {
    # 插件数据合同：取某通道最近 Count 帧原始数据（double[]）
    #
    # 契约（本函数是插件数据唯一入口，必须守住）：永远返回 double[]，
    # 绝不返回 $null、绝不向上抛错。调用方（频谱插件 get_spectrum_analysis）
    # 在 StrictMode 下直接读 $samples.Count，$null 会抛「在此对象上找不到属性
    # "Count"」并在每次重绘刷屏（runtime_error.log 已堆到数千条）。
    # 缓冲区未初始化 / 通道越界 / 列表为空 一律按「无数据」返回空数组。
    param([int]$Channel = 0, [int]$Count = 4096)

    $empty = [double[]]::new(0)
    try {
        if ($null -eq $script:wave_ch_values -or $Channel -lt 0) {
            return $empty
        }
        if ($Channel -ge $script:wave_ch_values.Count) {
            return $empty
        }

        $list = $script:wave_ch_values[$Channel]
        if ($null -eq $list) {
            return $empty
        }

        $take = [Math]::Min([Math]::Max(1, $Count), $list.Count)
        if ($take -le 0) {
            return $empty
        }

        $start = $list.Count - $take
        return [double[]]$list.GetRange($start, $take).ToArray()
    }
    catch {
        # 任何意外（含 StrictMode 首读未定义变量）都退化成「无数据」，绝不让插件崩
        return $empty
    }
}

function Register-WavePlugin {
    # 插件注册：波形窗口每次打开时重扫 plugins/，同名列覆盖
    param([string]$Name, [string]$Description = '', [scriptblock]$OpenPanel)

    if ([string]::IsNullOrWhiteSpace($Name) -or -not $OpenPanel) {
        return $null
    }

    foreach ($existing in $script:wave_plugins) {
        if ($existing.Name -eq $Name) {
            $existing.Description = $Description
            $existing.OpenPanel = $OpenPanel
            return $existing
        }
    }

    $plugin = [pscustomobject]@{ Name = $Name; Description = $Description; OpenPanel = $OpenPanel }
    [void]$script:wave_plugins.Add($plugin)
    return $plugin
}

function get_wave_y_range {
    param(
        [bool[]]$enabled,
        [int]$total,
        [int]$channel_count
    )

    $any_enabled = $false
    foreach ($flag in $enabled) {
        if ($flag) {
            $any_enabled = $true
            break
        }
    }

    if ($script:wave_auto_y) {
        $vmin = [double]::PositiveInfinity
        $vmax = [double]::NegativeInfinity
        if ($total -gt 0 -and $any_enabled) {
            $probe_visible = [Math]::Min($total, $script:wave_window_frames)
            $probe_start = $total - $probe_visible
            for ($j = 0; $j -lt $probe_visible; $j++) {
                for ($c = 0; $c -lt $channel_count; $c++) {
                    if (-not $enabled[$c]) {
                        continue
                    }
                    $v = $script:wave_ch_values[$c][$probe_start + $j]
                    if ([double]::IsNaN($v) -or [double]::IsInfinity($v)) {
                        continue
                    }
                    if ($v -lt $vmin) { $vmin = $v }
                    if ($v -gt $vmax) { $vmax = $v }
                }
            }
        }

        if ($vmin -gt $vmax) {
            $vmin = -1.0
            $vmax = 1.0
        }
        elseif ($vmin -eq $vmax) {
            if ($vmin -eq 0.0) {
                $vmin = -1.0
                $vmax = 1.0
            }
            else {
                $delta = [Math]::Abs($vmin) * 0.1 + 0.000001
                $vmin -= $delta
                $vmax += $delta
            }
        }
        else {
            $pad = ($vmax - $vmin) * 0.06
            $vmin -= $pad
            $vmax += $pad
        }
        return [pscustomobject]@{ Min = $vmin; Max = $vmax }
    }

    $y_min = $script:wave_y_min
    $y_max = $script:wave_y_max
    if ($y_max -le $y_min) {
        $y_max = $y_min + 1.0
    }
    return [pscustomobject]@{ Min = $y_min; Max = $y_max }
}

