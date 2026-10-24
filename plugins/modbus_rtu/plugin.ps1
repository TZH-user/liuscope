# ============================================================
# Modbus RTU 插件（modbus_rtu v1）
# 轻量 Modbus RTU 主站调试面板（独立窗体，不改宿主）：
#   - 功能码 01/02/03/04 读线圈/离散输入/保持寄存器/输入寄存器
#           05/06 写单线圈/单寄存器，0F/10 写多线圈/多寄存器
#   - CRC16-Modbus（poly 0xA001）自动计算与响应校验
#   - 寄存器多格式解码：HEX / 十进制 / 有符号 / float32(ABCD) / float32(CDAB)
#   - 轮询（500ms/1s/2s），事务回写主窗口日志（TX/RX 条目，主界面 Ctrl+F 可检索）
#   - 异常帧解析（01~0B 错误码中文说明）
#
# 用法：主窗口连接串口（设备侧为从站）→ 波形窗口 → 插件 → Modbus。
#
# 插件规范（必须遵守，均为 StrictMode/作用域踩坑结论）：
#   1. 插件自身 $script: 状态变量必须在【顶层】预置 $null
#   2. 事件 handler 里只访问 $script: 状态，不得引用函数局部变量
#   3. 顶层只做注册与常量定义；一切资源在 -OpenPanel 里申请、关闭时释放
#   4. 内部错误自行 try/catch，不向外抛（异常隔离由宿主兜底）
# ============================================================

$plugin_api_version = 1

$script:modbus_form = $null
$script:modbus_poll_timer = $null
$script:modbus_result_box = $null
$script:modbus_status_label = $null
$script:modbus_frame_label = $null
$script:modbus_text_slave = $null
$script:modbus_combo_fc = $null
$script:modbus_text_start = $null
$script:modbus_text_count = $null
$script:modbus_text_value = $null
$script:modbus_check_poll = $null
$script:modbus_combo_period = $null
$script:modbus_combo_decode = $null
# ---- 实时监视（live watch，静态事务之外的动态页） ----
$script:modbus_tab_ctrl = $null
$script:modbus_watch_grid = $null
$script:modbus_watch_timer = $null
$script:modbus_watch_check = $null
$script:modbus_watch_period = $null
$script:modbus_watch_name = $null
$script:modbus_watch_items = New-Object 'System.Collections.Generic.List[object]'
$script:modbus_watch_rows = @{}

# ---------- CRC16-Modbus：poly 0xA001（反射 0x8005），初值 0xFFFF ----------
function get_modbus_crc16 {
    param([byte[]]$data)

    $crc = 0xFFFF
    foreach ($b in $data) {
        $crc = $crc -bxor $b
        for ($i = 0; $i -lt 8; $i++) {
            if (($crc -band 0x0001) -ne 0) {
                $crc = (($crc -shr 1) -bxor 0xA001) -band 0xFFFF
            }
            else {
                $crc = ($crc -shr 1) -band 0xFFFF
            }
        }
    }
    # RTU 帧尾 CRC 低字节在前
    return [byte[]](($crc -band 0xFF), (($crc -shr 8) -band 0xFF))
}

function test_modbus_crc {
    param([byte[]]$frame)

    if (-not $frame -or $frame.Length -lt 4) {
        return $false
    }

    $body = New-Object byte[] ($frame.Length - 2)
    [Array]::Copy($frame, $body, $body.Length)
    $crc = get_modbus_crc16 -data $body
    return ($crc[0] -eq $frame[$frame.Length - 2] -and $crc[1] -eq $frame[$frame.Length - 1])
}

function parse_modbus_number {
    # 十进制或 0x 十六进制 → int；非法返回 $null
    param([string]$text)

    $t = ([string]$text).Trim()
    if ($t.Length -eq 0) {
        return $null
    }

    if ($t -match '^0[xX][0-9A-Fa-f]+$') {
        return [Convert]::ToInt32($t.Substring(2), 16)
    }
    $value = 0
    if ([int]::TryParse($t, [ref]$value)) {
        return $value
    }
    return $null
}

function build_modbus_request {
    # 组 RTU 请求帧（含 CRC）。返回 @{ Bytes = byte[]; Error = '' }；value_list 供 0F/10 用
    param(
        [int]$slave,
        [int]$fc,
        [int]$start,
        [int]$count,
        [string]$value_text,
        [string[]]$value_list
    )

    $error = ''
    $payload = New-Object System.Collections.Generic.List[byte]
    # RTU 帧 = 从站地址(1B) + 功能码(1B) + 数据 + CRC16（低字节在前）
    [void]$payload.Add([byte]$slave)
    [void]$payload.Add([byte]$fc)
    $start = $start -band 0xFFFF
    [void]$payload.Add([byte](($start -shr 8) -band 0xFF))
    [void]$payload.Add([byte]($start -band 0xFF))

    switch ($fc) {
        1 { if ($count -lt 1 -or $count -gt 2000) { $error = '线圈数量须在 1~2000' } }
        2 { if ($count -lt 1 -or $count -gt 2000) { $error = '离散输入数量须在 1~2000' } }
        3 { if ($count -lt 1 -or $count -gt 125) { $error = '寄存器数量须在 1~125' } }
        4 { if ($count -lt 1 -or $count -gt 125) { $error = '寄存器数量须在 1~125' } }
        5 {
            # 0xFF00 = 置位，0x0000 = 复位
            $v = ([string]$value_text).Trim().ToLower()
            if ($v -eq '1' -or $v -eq 'on' -or $v -eq 'true') { [void]$payload.Add(0xFF) }
            else { [void]$payload.Add(0x00) }
            [void]$payload.Add(0x00)
        }
        6 {
            $v = parse_modbus_number -text $value_text
            if ($null -eq $v -or $v -lt 0 -or $v -gt 0xFFFF) { $error = '写入值须为 0~65535（支持 0x 十六进制）' }
            else {
                [void]$payload.Add([byte](($v -shr 8) -band 0xFF))
                [void]$payload.Add([byte]($v -band 0xFF))
            }
        }
        15 {
            if (-not $value_list -or $value_list.Count -eq 0) {
                $error = '写多线圈需在值里给 0/1 列表，如 1,0,1,1'
            }
            elseif ($value_list.Count -gt 1968) {
                $error = '线圈最多 1968 个'
            }
            else {
                $bit_count = $value_list.Count
                [void]$payload.Add([byte](($bit_count -shr 8) -band 0xFF))
                [void]$payload.Add([byte]($bit_count -band 0xFF))
                [void]$payload.Add([byte][Math]::Ceiling($bit_count / 8.0))
                $byte_val = 0
                $bit_pos = 0
                foreach ($bit_text in $value_list) {
                    $bv = ([string]$bit_text).Trim().ToLower()
                    if ($bv -eq '1' -or $bv -eq 'on' -or $bv -eq 'true') {
                        $byte_val = $byte_val -bor (1 -shl $bit_pos)
                    }
                    $bit_pos++
                    if ($bit_pos -eq 8) {
                        [void]$payload.Add([byte]$byte_val)
                        $byte_val = 0
                        $bit_pos = 0
                    }
                }
                if ($bit_pos -gt 0) {
                    [void]$payload.Add([byte]$byte_val)
                }
            }
        }
        16 {
            if (-not $value_list -or $value_list.Count -eq 0) {
                $error = '写多寄存器需在值里给逗号分隔列表，如 100,0x2000,-5'
            }
            elseif ($value_list.Count -gt 123) {
                $error = '寄存器最多 123 个'
            }
            else {
                $reg_count = $value_list.Count
                [void]$payload.Add([byte](($reg_count -shr 8) -band 0xFF))
                [void]$payload.Add([byte]($reg_count -band 0xFF))
                [void]$payload.Add([byte]($reg_count * 2))
                foreach ($reg_text in $value_list) {
                    $rv = parse_modbus_number -text $reg_text
                    if ($null -eq $rv) { $error = ('无法解析寄存器值: ' + $reg_text); break }
                    $rv = $rv -band 0xFFFF
                    [void]$payload.Add([byte](($rv -shr 8) -band 0xFF))
                    [void]$payload.Add([byte]($rv -band 0xFF))
                }
            }
        }
        default { $error = '不支持的功能码' }
    }

    if ($error -ne '') {
        return @{ Bytes = $null; Error = $error }
    }

    if ($fc -ge 1 -and $fc -le 4) {
        [void]$payload.Add([byte](($count -shr 8) -band 0xFF))
        [void]$payload.Add([byte]($count -band 0xFF))
    }

    $body = $payload.ToArray()
    $crc = get_modbus_crc16 -data $body
    $frame = New-Object byte[] ($body.Length + 2)
    [Array]::Copy($body, $frame, $body.Length)
    $frame[$body.Length] = $crc[0]
    $frame[$body.Length + 1] = $crc[1]
    return @{ Bytes = $frame; Error = '' }
}

function get_modbus_exception_text {
    param([int]$code)

    switch ($code) {
        1 { return '01 非法功能' }
        2 { return '02 非法数据地址' }
        3 { return '03 非法数据值' }
        4 { return '04 从站设备故障' }
        5 { return '05 已接收，处理中' }
        6 { return '06 从站忙' }
        8 { return '08 存储奇偶校验错误' }
        10 { return '0A 网关不可达' }
        11 { return '0B 网关目标无响应' }
        default { return ('{0:X2} 未知异常' -f $code) }
    }
}

function parse_modbus_response {
    # 校验并解包响应。返回 @{ Ok; Error; Exception; RegBytes; Coils; ByteCount }
    param([byte[]]$resp, [int]$fc, [int]$slave, [int]$quantity)

    $out = @{ Ok = $false; Error = ''; Exception = -1; RegBytes = $null; Coils = $null; ByteCount = 0 }
    if (-not $resp -or $resp.Length -lt 4) {
        $out.Error = '响应太短'
        return $out
    }
    if (-not (test_modbus_crc -frame $resp)) {
        $out.Error = 'CRC 校验失败'
        return $out
    }
    if ($resp[0] -ne $slave) {
        $out.Error = ('从站地址不匹配（收到 {0}，期望 {1}）' -f $resp[0], $slave)
        return $out
    }

    if (($resp[1] -band 0x80) -ne 0) {
        $code = -1
        if ($resp.Length -ge 3) { $code = [int]$resp[2] }
        $out.Exception = $code
        $out.Error = ('从站异常: ' + (get_modbus_exception_text -code $code))
        return $out
    }

    if ($fc -ge 1 -and $fc -le 4) {
        if ($resp.Length -lt 5) {
            $out.Error = '读响应太短'
            return $out
        }
        $bc = [int]$resp[2]
        if ($resp.Length -lt (3 + $bc + 2)) {
            $out.Error = '响应字节数不完整'
            return $out
        }
        $out.ByteCount = $bc
        $out.RegBytes = New-Object byte[] $bc
        [Array]::Copy($resp, 3, $out.RegBytes, 0, $bc)

        if ($fc -le 2) {
            # 线圈打包：每字节 LSB 在前
            $coils = New-Object System.Collections.Generic.List[int]
            for ($i = 0; $i -lt $quantity; $i++) {
                $byte_index = [int]($i / 8)
                if ($byte_index -ge $bc) { break }
                $bit = ($out.RegBytes[$byte_index] -shr ($i % 8)) -band 0x01
                [void]$coils.Add($bit)
            }
            $out.Coils = $coils.ToArray()
        }
    }

    $out.Ok = $true
    return $out
}

function decode_modbus_registers {
    # 寄存器字节流 → 按所选格式解码为可读文本
    param([byte[]]$reg_bytes, [string]$mode)

    if (-not $reg_bytes -or $reg_bytes.Count -eq 0) {
        return ''
    }

    $parts = New-Object System.Collections.Generic.List[string]
    $reg_count = [int]($reg_bytes.Count / 2)
    for ($i = 0; $i -lt $reg_count; $i++) {
        $u16 = (([int]$reg_bytes[$i * 2]) -shl 8) -bor [int]$reg_bytes[$i * 2 + 1]
        switch ($mode) {
            'HEX' { [void]$parts.Add(('0x{0:X4}' -f $u16)) }
            '有符号' {
                $s16 = $u16
                if ($u16 -gt 0x7FFF) { $s16 = $u16 - 0x10000 }
                [void]$parts.Add([string]$s16)
            }
            'float32(ABCD)' {
                if ($i * 4 + 3 -lt $reg_bytes.Count) {
                    $b = [byte[]]@($reg_bytes[$i * 4 + 3], $reg_bytes[$i * 4 + 2], $reg_bytes[$i * 4 + 1], $reg_bytes[$i * 4])
                    [void]$parts.Add(([BitConverter]::ToSingle($b, 0)).ToString('G9'))
                }
            }
            'float32(CDAB)' {
                if ($i * 4 + 3 -lt $reg_bytes.Count) {
                    $b = [byte[]]@($reg_bytes[$i * 4 + 1], $reg_bytes[$i * 4], $reg_bytes[$i * 4 + 3], $reg_bytes[$i * 4 + 2])
                    [void]$parts.Add(([BitConverter]::ToSingle($b, 0)).ToString('G9'))
                }
            }
            default { [void]$parts.Add([string]$u16) }
        }
    }
    return ($parts -join ', ')
}

# ---------- 实时监视（live watch）：键匹配 + 合并轮询 ----------
# 监视项以「从站:功能码:起始地址」为键；轮询引擎把同键域的项合并成一次读请求，
# 响应字节按地址偏移切回各项——这就是调试器 Watch 窗口的"关键匹配更新"。

function get_modbus_watch_key {
    param([int]$slave, [int]$fc, [int]$start)

    return ('{0}:{1}:{2}' -f $slave, $fc, $start)
}

function upsert_modbus_watch_item {
    # 同键覆盖（改数量/格式 = 原位更新），新键追加
    param([System.Collections.Generic.List[object]]$list, $item)

    for ($i = 0; $i -lt $list.Count; $i++) {
        if ($list[$i].Key -eq $item.Key) {
            $item.LastValue = $list[$i].LastValue
            $item.LastStatus = $list[$i].LastStatus
            $list[$i] = $item
            return $list
        }
    }
    $list.Add($item)
    return $list
}

function merge_modbus_watch_groups {
    # 把监视项按 (从站, 功能码) 分组，组内按地址排序后合并重叠/近邻（间隙≤4）区间，
    # 单次读上限：寄存器 125 / 线圈 2000。返回 pscustomobject 组列表。
    param([object[]]$items)

    $sorted = @($items | Sort-Object -Property Slave, Fc, Start)
    $groups = New-Object 'System.Collections.Generic.List[object]'
    foreach ($item in $sorted) {
        $limit = 125
        if ($item.Fc -le 2) { $limit = 2000 }
        $merged = $false
        foreach ($g in $groups) {
            if ($g.Slave -ne $item.Slave -or $g.Fc -ne $item.Fc) { continue }
            $group_end = $g.Start + $g.Count
            $item_end = $item.Start + $item.Count
            # 重叠或间隙 ≤4 才合并；合并后总跨度不得超上限
            if (($item.Start -le ($group_end + 4)) -and (($item_end - $g.Start) -le $limit)) {
                if ($item_end -gt $group_end) {
                    $g.Count = $item_end - $g.Start
                }
                [void]$g.Items.Add($item)
                $merged = $true
                break
            }
        }
        if (-not $merged) {
            $g = [pscustomobject]@{
                Slave = $item.Slave
                Fc = $item.Fc
                Start = $item.Start
                Count = $item.Count
                Items = New-Object 'System.Collections.Generic.List[object]'
            }
            [void]$g.Items.Add($item)
            [void]$groups.Add($g)
        }
    }
    return $groups
}

function slice_modbus_watch_value {
    # 从合并读响应里按地址偏移切回单个监视项的值（寄存器切字节、线圈切位）
    param([byte[]]$reg_bytes, [int]$fc, [int]$group_start, $item)

    if ($fc -le 2) {
        $offset = $item.Start - $group_start
        $bits = New-Object System.Collections.Generic.List[int]
        for ($i = 0; $i -lt $item.Count; $i++) {
            $idx = $offset + $i
            $byte_index = [int]($idx / 8)
            if ($byte_index -ge $reg_bytes.Count) { break }
            [void]$bits.Add((($reg_bytes[$byte_index] -shr ($idx % 8)) -band 0x01))
        }
        $text = ($bits -join ',')
        return @{ Text = $text; Raw = $text }
    }

    $offset = ($item.Start - $group_start) * 2
    $len = $item.Count * 2
    if (($offset -lt 0) -or (($offset + $len) -gt $reg_bytes.Count)) {
        return @{ Text = '—'; Raw = '' }
    }
    $slice = New-Object byte[] $len
    [Array]::Copy($reg_bytes, $offset, $slice, 0, $len)
    return @{
        Text = (decode_modbus_registers -reg_bytes $slice -mode $item.Decode)
        Raw = (bytes_to_hex_text -bytes $slice)
    }
}

function get_modbus_watch_flash_color {
    # 值变化闪烁：数值可比较 → 涨绿/跌红，其余 → 琥珀；不变 → Empty（恢复继承色）
    param([string]$old_text, [string]$new_text)

    if ($old_text -eq $new_text) {
        return [System.Drawing.Color]::Empty
    }

    $old_num = [double]0
    $new_num = [double]0
    $old_ok = [double]::TryParse($old_text, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$old_num)
    $new_ok = [double]::TryParse($new_text, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$new_num)
    if ($old_ok -and $new_ok) {
        if ($new_num -gt $old_num) { return [System.Drawing.Color]::FromArgb(128, 220, 128) }
        if ($new_num -lt $old_num) { return [System.Drawing.Color]::FromArgb(235, 118, 108) }
    }
    return [System.Drawing.Color]::FromArgb(232, 198, 110)
}

function send_modbus_wire_transaction {
    # 线上事务：清残留 → 写请求 → 有界读响应。UI 线程同步执行（WinForms 定时器
    # 在同步代码期间不会抢占，不存在接收循环抢字节的竞态）。返回响应 byte[]（可为空）。
    param([byte[]]$frame, [int]$timeout_ms = 600)

    try { $script:serial_port.DiscardInBuffer() } catch {}
    $script:serial_port.Write([byte[]]$frame, 0, $frame.Length)

    $deadline = [datetime]::Now.AddMilliseconds($timeout_ms)
    $idle_at = [datetime]::Now.AddMilliseconds(150)
    $resp = New-Object System.Collections.Generic.List[byte]
    $expected = -1
    while ([datetime]::Now -lt $deadline) {
        $pending = 0
        try { $pending = $script:serial_port.BytesToRead } catch { break }
        if ($pending -gt 0) {
            $buf = New-Object byte[] $pending
            $n = $script:serial_port.Read($buf, 0, $pending)
            for ($i = 0; $i -lt $n; $i++) { [void]$resp.Add($buf[$i]) }
            $idle_at = [datetime]::Now.AddMilliseconds(150)
            if ($expected -lt 0 -and $resp.Count -ge 3) {
                if (($resp[1] -band 0x80) -ne 0) { $expected = 5 }
                else { $expected = 3 + [int]$resp[2] + 2 }
            }
            if ($expected -ge 0 -and $resp.Count -ge $expected) { break }
        }
        else {
            Start-Sleep -Milliseconds 10
        }
        if ($resp.Count -gt 0 -and [datetime]::Now -gt $idle_at) { break }
    }
    return $resp.ToArray()
}

function get_modbus_serial_ready {
    return ($script:serial_port -and $script:serial_port.IsOpen)
}

function update_modbus_status {
    if (get_modbus_serial_ready) {
        $script:modbus_status_label.Text = ('已连接 {0} @ {1}' -f $script:connected_port_name, $script:connected_baud_rate)
        $script:modbus_status_label.ForeColor = [System.Drawing.Color]::FromArgb(120, 200, 120)
    }
    else {
        $script:modbus_status_label.Text = '串口未连接（先在主窗口连接）'
        $script:modbus_status_label.ForeColor = [System.Drawing.Color]::FromArgb(230, 130, 110)
    }
}

function append_modbus_result {
    param([string]$text)

    if (-not $script:modbus_result_box -or $script:modbus_result_box.IsDisposed) {
        return
    }

    $stamp = [datetime]::Now.ToString('HH:mm:ss.fff')
    $script:modbus_result_box.AppendText(('[{0}] {1}' -f $stamp, $text) + "`r`n")
}

function run_modbus_ui_transaction {
    # UI 层事务：读面板字段 → 组帧 → 线上事务 → 解码 → 面板与主日志回写
    if (-not (get_modbus_serial_ready)) {
        update_modbus_status
        return
    }

    try {
        $slave = parse_modbus_number -text $script:modbus_text_slave.Text
        $start = parse_modbus_number -text $script:modbus_text_start.Text
        $count = parse_modbus_number -text $script:modbus_text_count.Text
        if ($null -eq $slave -or $slave -lt 1 -or $slave -gt 247) {
            append_modbus_result -text '从站地址须为 1~247'
            return
        }
        if ($null -eq $start -or $start -lt 0 -or $start -gt 0xFFFF) {
            append_modbus_result -text '起始地址须为 0~65535（支持 0x）'
            return
        }
        if ($null -eq $count -or $count -lt 1) { $count = 1 }

        $fc_index = $script:modbus_combo_fc.SelectedIndex
        if ($fc_index -lt 0) { $fc_index = 2 }
        $fc_map = @(1, 2, 3, 4, 5, 6, 15, 16)
        $fc = $fc_map[$fc_index]

        $value_text = [string]$script:modbus_text_value.Text
        $value_list = @()
        if ($fc -eq 15 -or $fc -eq 16) {
            $value_list = ($value_text -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_.Length -gt 0 }
            if ($value_list.Count -gt 0) {
                $count = $value_list.Count
            }
        }

        $built = build_modbus_request -slave $slave -fc $fc -start $start -count $count -value_text $value_text -value_list $value_list
        if ($built.Error -ne '') {
            append_modbus_result -text ('组帧失败: ' + $built.Error)
            return
        }

        $frame = $built.Bytes
        $req_hex = bytes_to_hex_text -bytes $frame
        $script:modbus_frame_label.Text = ('发送: ' + $req_hex)

        $resp_arr = send_modbus_wire_transaction -frame $frame -timeout_ms 600
        $resp_hex = bytes_to_hex_text -bytes $resp_arr

        # 事务回写主窗口日志（TX/RX 条目 → 主界面 Ctrl+F 可检索）
        try {
            add_message_entry -direction 'TX' -text ('[Modbus] ' + $req_hex)
            if ($resp_arr.Length -gt 0) {
                add_message_entry -direction 'RX' -text ('[Modbus] ' + $resp_hex)
            }
        }
        catch {
        }

        if ($resp_arr.Length -eq 0) {
            append_modbus_result -text ('无响应（从站 {0}，fc {1:X2}，超时 600ms）' -f $slave, $fc)
            return
        }

        append_modbus_result -text ('请求 ' + $req_hex)
        $parsed = parse_modbus_response -resp $resp_arr -fc $fc -slave $slave -quantity $count
        if (-not $parsed.Ok) {
            append_modbus_result -text ('响应 ' + $resp_hex + '  →  ' + $parsed.Error)
            return
        }

        if ($fc -le 2) {
            $coil_text = ($parsed.Coils | ForEach-Object { $_ }) -join ','
            append_modbus_result -text ('响应 ' + $resp_hex)
            append_modbus_result -text ('线圈/离散输入[{0}]: {1}' -f $count, $coil_text)
        }
        elseif ($fc -le 4) {
            $decode_mode = '十进制'
            if ($script:modbus_combo_decode.SelectedItem) { $decode_mode = [string]$script:modbus_combo_decode.SelectedItem }
            $decoded = decode_modbus_registers -reg_bytes $parsed.RegBytes -mode $decode_mode
            append_modbus_result -text ('响应 ' + $resp_hex)
            append_modbus_result -text ('寄存器[{0}] {1}: {2}' -f $count, $decode_mode, $decoded)
        }
        else {
            append_modbus_result -text ('响应 ' + $resp_hex + '  →  写入成功')
        }
    }
    catch {
        append_modbus_result -text ('事务异常: ' + $_.Exception.Message)
    }
}

function get_modbus_watch_key_text {
    param($item)

    return ('S{0} FC{1:X2} @{2} ×{3}' -f $item.Slave, $item.Fc, $item.Start, $item.Count)
}

function rebuild_modbus_watch_grid {
    # 从监视项列表整表重建（增删后调用；项数少，整表重建最简单可靠）
    $grid = $script:modbus_watch_grid
    if (-not $grid -or $grid.IsDisposed) {
        return
    }

    $grid.SuspendLayout()
    $grid.Rows.Clear()
    $script:modbus_watch_rows.Clear()
    $sorted = @($script:modbus_watch_items | Sort-Object -Property Key)
    foreach ($item in $sorted) {
        $row_index = $grid.Rows.Add($item.Name, (get_modbus_watch_key_text -item $item), $item.LastValue, $item.Decode, $item.LastStatus)
        $row = $grid.Rows[$row_index]
        $row.Tag = $item
        $script:modbus_watch_rows[$item.Key] = $row
    }
    $grid.ResumeLayout()
}

function set_modbus_watch_row {
    param($item, [string]$text, [string]$status)

    $row = $script:modbus_watch_rows[$item.Key]
    if (-not $row) {
        return
    }

    $value_cell = $row.Cells[2]
    $flash = get_modbus_watch_flash_color -old_text $item.LastValue -new_text $text
    $value_cell.Value = $text
    $value_cell.Style.ForeColor = $flash
    $row.Cells[4].Value = $status
    $item.LastValue = $text
    $item.LastStatus = $status
}

function add_modbus_watch_from_ui {
    # 「← 从事务添加」：把事务区的 从站/功能码/起始地址/数量(+解码格式) 收进监视表。
    # 只收读功能码（01~04）；写操作属于静态事务，不进轮询。
    if ($script:modbus_combo_fc.SelectedIndex -lt 0) {
        return
    }
    if ($script:modbus_combo_fc.SelectedIndex -gt 3) {
        append_modbus_result -text '监视只支持读功能码 01~04（写操作请在事务页执行）'
        $script:modbus_tab_ctrl.SelectedIndex = 0
        return
    }

    $slave = parse_modbus_number -text $script:modbus_text_slave.Text
    $start = parse_modbus_number -text $script:modbus_text_start.Text
    $count = parse_modbus_number -text $script:modbus_text_count.Text
    if ($null -eq $slave -or $slave -lt 1 -or $slave -gt 247) {
        append_modbus_result -text '从站地址须为 1~247'
        return
    }
    if ($null -eq $start -or $start -lt 0 -or $start -gt 0xFFFF) {
        append_modbus_result -text '起始地址须为 0~65535（支持 0x）'
        return
    }
    if ($null -eq $count -or $count -lt 1) { $count = 1 }

    $fc_map = @(1, 2, 3, 4)
    $fc = $fc_map[$script:modbus_combo_fc.SelectedIndex]
    $decode = '十进制'
    if ($script:modbus_combo_decode.SelectedItem) { $decode = [string]$script:modbus_combo_decode.SelectedItem }
    $name = ([string]$script:modbus_watch_name.Text).Trim()
    if ($name.Length -eq 0) {
        $name = ('@{0}' -f $start)
    }

    $item = [pscustomobject]@{
        Key = (get_modbus_watch_key -slave $slave -fc $fc -start $start)
        Name = $name
        Slave = $slave
        Fc = $fc
        Start = $start
        Count = $count
        Decode = $decode
        LastValue = ''
        LastStatus = ''
    }
    [void](upsert_modbus_watch_item -list $script:modbus_watch_items -item $item)
    rebuild_modbus_watch_grid
}

function remove_selected_modbus_watch {
    $grid = $script:modbus_watch_grid
    if (-not $grid -or $grid.SelectedRows.Count -eq 0) {
        return
    }

    $keys = New-Object System.Collections.Generic.List[string]
    foreach ($row in $grid.SelectedRows) {
        if ($row.Tag) {
            [void]$keys.Add($row.Tag.Key)
        }
    }
    $kept = New-Object 'System.Collections.Generic.List[object]'
    foreach ($item in $script:modbus_watch_items) {
        if (-not $keys.Contains($item.Key)) {
            [void]$kept.Add($item)
        }
    }
    $script:modbus_watch_items = $kept
    rebuild_modbus_watch_grid
}

function clear_modbus_watch {
    $script:modbus_watch_items.Clear()
    rebuild_modbus_watch_grid
}

function run_modbus_watch_cycle {
    # 一轮监视：合并读 → 按键匹配回填。与手动事务共用串口；手动事务在 UI 线程
    # 同步执行期间本定时器不会抢占，但事务的 DiscardInBuffer 可能吃掉一轮在途
    # 响应——该轮监视保持旧值，下一周期自愈。
    if (-not $script:modbus_watch_check -or -not $script:modbus_watch_check.Checked) {
        return
    }
    if (-not (get_modbus_serial_ready)) {
        return
    }

    $enabled = @($script:modbus_watch_items)
    if ($enabled.Count -eq 0) {
        return
    }

    try {
        $groups = merge_modbus_watch_groups -items $enabled
        foreach ($group in $groups) {
            $built = build_modbus_request -slave $group.Slave -fc $group.Fc -start $group.Start -count $group.Count -value_text '' -value_list @()
            if ($built.Error -ne '') { continue }

            $resp = send_modbus_wire_transaction -frame $built.Bytes -timeout_ms 400
            $stamp = [datetime]::Now.ToString('HH:mm:ss')
            if ($resp.Length -eq 0) {
                foreach ($item in $group.Items) {
                    set_modbus_watch_row -item $item -text $item.LastValue -status ('无响应 ' + $stamp)
                }
                continue
            }

            $parsed = parse_modbus_response -resp $resp -fc $group.Fc -slave $group.Slave -quantity $group.Count
            if (-not $parsed.Ok) {
                foreach ($item in $group.Items) {
                    set_modbus_watch_row -item $item -text $item.LastValue -status ($parsed.Error + ' ' + $stamp)
                }
                continue
            }

            foreach ($item in $group.Items) {
                $val = slice_modbus_watch_value -reg_bytes $parsed.RegBytes -fc $group.Fc -group_start $group.Start -item $item
                set_modbus_watch_row -item $item -text $val.Text -status $stamp
            }
        }
    }
    catch {
        append_modbus_result -text ('监视轮询异常: ' + $_.Exception.Message)
    }
}

function show_modbus_panel {
    if ($script:modbus_form -and -not $script:modbus_form.IsDisposed) {
        $script:modbus_form.Show()
        $script:modbus_form.Activate()
        return
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Modbus RTU 主站 — 插件'
    $form.StartPosition = 'CenterParent'
    $form.Size = New-Object System.Drawing.Size(790, 560)
    $form.MinimumSize = New-Object System.Drawing.Size(640, 460)
    $form.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $form.Font = new_font -name 'Microsoft YaHei UI' -size 9

    $toolbar = New-Object System.Windows.Forms.Panel
    $toolbar.Dock = 'Top'
    $toolbar.Height = 104
    $toolbar.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $dim_fg = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $input_bg = [System.Drawing.Color]::FromArgb(38, 45, 58)
    $text_fg = [System.Drawing.Color]::FromArgb(225, 230, 240)

    # 第一行：从站 / 功能码 / 起始地址 / 数量 / 值
    $label_slave = New-Object System.Windows.Forms.Label
    $label_slave.Text = '从站'
    $label_slave.Location = New-Object System.Drawing.Point(10, 14)
    $label_slave.AutoSize = $true
    $label_slave.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_slave)

    $script:modbus_text_slave = New-Object System.Windows.Forms.TextBox
    $script:modbus_text_slave.Location = New-Object System.Drawing.Point(44, 10)
    $script:modbus_text_slave.Size = New-Object System.Drawing.Size(40, 24)
    $script:modbus_text_slave.Text = '1'
    $script:modbus_text_slave.BackColor = $input_bg
    $script:modbus_text_slave.ForeColor = $text_fg
    $script:modbus_text_slave.BorderStyle = 'FixedSingle'
    $toolbar.Controls.Add($script:modbus_text_slave)

    $label_fc = New-Object System.Windows.Forms.Label
    $label_fc.Text = '功能码'
    $label_fc.Location = New-Object System.Drawing.Point(94, 14)
    $label_fc.AutoSize = $true
    $label_fc.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_fc)

    $script:modbus_combo_fc = New-Object System.Windows.Forms.ComboBox
    $script:modbus_combo_fc.Location = New-Object System.Drawing.Point(142, 10)
    $script:modbus_combo_fc.Size = New-Object System.Drawing.Size(180, 24)
    $script:modbus_combo_fc.DropDownStyle = 'DropDownList'
    [void]$script:modbus_combo_fc.Items.AddRange(@(
        '01 读线圈', '02 读离散输入', '03 读保持寄存器', '04 读输入寄存器',
        '05 写单线圈', '06 写单寄存器', '0F 写多线圈', '10 写多寄存器'))
    $script:modbus_combo_fc.SelectedIndex = 2
    $script:modbus_combo_fc.BackColor = $input_bg
    $script:modbus_combo_fc.ForeColor = $text_fg
    $script:modbus_combo_fc.FlatStyle = 'Flat'
    $toolbar.Controls.Add($script:modbus_combo_fc)

    $label_start = New-Object System.Windows.Forms.Label
    $label_start.Text = '起始地址'
    $label_start.Location = New-Object System.Drawing.Point(332, 14)
    $label_start.AutoSize = $true
    $label_start.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_start)

    $script:modbus_text_start = New-Object System.Windows.Forms.TextBox
    $script:modbus_text_start.Location = New-Object System.Drawing.Point(392, 10)
    $script:modbus_text_start.Size = New-Object System.Drawing.Size(64, 24)
    $script:modbus_text_start.Text = '0'
    $script:modbus_text_start.BackColor = $input_bg
    $script:modbus_text_start.ForeColor = $text_fg
    $script:modbus_text_start.BorderStyle = 'FixedSingle'
    $toolbar.Controls.Add($script:modbus_text_start)

    $label_count = New-Object System.Windows.Forms.Label
    $label_count.Text = '数量'
    $label_count.Location = New-Object System.Drawing.Point(466, 14)
    $label_count.AutoSize = $true
    $label_count.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_count)

    $script:modbus_text_count = New-Object System.Windows.Forms.TextBox
    $script:modbus_text_count.Location = New-Object System.Drawing.Point(506, 10)
    $script:modbus_text_count.Size = New-Object System.Drawing.Size(48, 24)
    $script:modbus_text_count.Text = '2'
    $script:modbus_text_count.BackColor = $input_bg
    $script:modbus_text_count.ForeColor = $text_fg
    $script:modbus_text_count.BorderStyle = 'FixedSingle'
    $toolbar.Controls.Add($script:modbus_text_count)

    $label_value = New-Object System.Windows.Forms.Label
    $label_value.Text = '值(写)'
    $label_value.Location = New-Object System.Drawing.Point(564, 14)
    $label_value.AutoSize = $true
    $label_value.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_value)

    $script:modbus_text_value = New-Object System.Windows.Forms.TextBox
    $script:modbus_text_value.Location = New-Object System.Drawing.Point(612, 10)
    $script:modbus_text_value.Size = New-Object System.Drawing.Size(150, 24)
    $script:modbus_text_value.Text = ''
    $script:modbus_text_value.BackColor = $input_bg
    $script:modbus_text_value.ForeColor = $text_fg
    $script:modbus_text_value.BorderStyle = 'FixedSingle'
    $toolbar.Controls.Add($script:modbus_text_value)

    # 第二行：事务 / 轮询 / 周期 / 解码 / 串口状态
    $button_run = New-Object System.Windows.Forms.Button
    $button_run.Text = '执行事务'
    $button_run.Location = New-Object System.Drawing.Point(10, 42)
    $button_run.Size = New-Object System.Drawing.Size(84, 26)
    $button_run.FlatStyle = 'Flat'
    $button_run.BackColor = [System.Drawing.Color]::FromArgb(14, 99, 156)
    $button_run.ForeColor = [System.Drawing.Color]::White
    $toolbar.Controls.Add($button_run)

    $script:modbus_check_poll = New-Object System.Windows.Forms.CheckBox
    $script:modbus_check_poll.Text = '轮询'
    $script:modbus_check_poll.Location = New-Object System.Drawing.Point(104, 45)
    $script:modbus_check_poll.AutoSize = $true
    $script:modbus_check_poll.ForeColor = $dim_fg
    $toolbar.Controls.Add($script:modbus_check_poll)

    $script:modbus_combo_period = New-Object System.Windows.Forms.ComboBox
    $script:modbus_combo_period.Location = New-Object System.Drawing.Point(158, 43)
    $script:modbus_combo_period.Size = New-Object System.Drawing.Size(70, 24)
    $script:modbus_combo_period.DropDownStyle = 'DropDownList'
    [void]$script:modbus_combo_period.Items.AddRange(@('500ms', '1s', '2s'))
    $script:modbus_combo_period.SelectedIndex = 0
    $script:modbus_combo_period.BackColor = $input_bg
    $script:modbus_combo_period.ForeColor = $text_fg
    $script:modbus_combo_period.FlatStyle = 'Flat'
    $toolbar.Controls.Add($script:modbus_combo_period)

    $label_decode = New-Object System.Windows.Forms.Label
    $label_decode.Text = '解码'
    $label_decode.Location = New-Object System.Drawing.Point(240, 48)
    $label_decode.AutoSize = $true
    $label_decode.ForeColor = $dim_fg
    $toolbar.Controls.Add($label_decode)

    $script:modbus_combo_decode = New-Object System.Windows.Forms.ComboBox
    $script:modbus_combo_decode.Location = New-Object System.Drawing.Point(280, 43)
    $script:modbus_combo_decode.Size = New-Object System.Drawing.Size(130, 24)
    $script:modbus_combo_decode.DropDownStyle = 'DropDownList'
    [void]$script:modbus_combo_decode.Items.AddRange(@('HEX', '十进制', '有符号', 'float32(ABCD)', 'float32(CDAB)'))
    $script:modbus_combo_decode.SelectedIndex = 1
    $script:modbus_combo_decode.BackColor = $input_bg
    $script:modbus_combo_decode.ForeColor = $text_fg
    $script:modbus_combo_decode.FlatStyle = 'Flat'
    $toolbar.Controls.Add($script:modbus_combo_decode)

    $script:modbus_status_label = New-Object System.Windows.Forms.Label
    $script:modbus_status_label.Location = New-Object System.Drawing.Point(424, 47)
    $script:modbus_status_label.AutoSize = $true
    $script:modbus_status_label.ForeColor = $dim_fg
    $toolbar.Controls.Add($script:modbus_status_label)

    # 第三行：请求帧预览
    $script:modbus_frame_label = New-Object System.Windows.Forms.Label
    $script:modbus_frame_label.Text = '发送: —'
    $script:modbus_frame_label.Location = New-Object System.Drawing.Point(10, 78)
    $script:modbus_frame_label.AutoSize = $true
    $script:modbus_frame_label.ForeColor = [System.Drawing.Color]::FromArgb(120, 200, 220)
    $script:modbus_frame_label.Font = new_font -name 'Consolas' -size 9
    $toolbar.Controls.Add($script:modbus_frame_label)

    $script:modbus_result_box = New-Object System.Windows.Forms.TextBox
    $script:modbus_result_box.Dock = 'Fill'
    $script:modbus_result_box.Multiline = $true
    $script:modbus_result_box.ReadOnly = $true
    $script:modbus_result_box.ScrollBars = 'Vertical'
    $script:modbus_result_box.WordWrap = $false
    $script:modbus_result_box.Font = new_font -name 'Consolas' -size 9
    $script:modbus_result_box.BackColor = [System.Drawing.Color]::FromArgb(16, 20, 28)
    $script:modbus_result_box.ForeColor = $text_fg
    $script:modbus_result_box.BorderStyle = 'None'

    # ---- 双页：事务（静态手动）/ 实时监视（动态 live watch） ----
    $script:modbus_tab_ctrl = New-Object System.Windows.Forms.TabControl
    $script:modbus_tab_ctrl.Dock = 'Fill'
    $tab_txn = New-Object System.Windows.Forms.TabPage
    $tab_txn.Text = '事务'
    $tab_txn.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    [void]$script:modbus_tab_ctrl.TabPages.Add($tab_txn)
    $tab_watch = New-Object System.Windows.Forms.TabPage
    $tab_watch.Text = '实时监视'
    $tab_watch.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    [void]$script:modbus_tab_ctrl.TabPages.Add($tab_watch)

    $tab_txn.Controls.Add($script:modbus_result_box)

    # 监视页工具条：名称 + 从事务添加 / 删除 / 清空 / 监视开关 / 周期
    $watch_strip = New-Object System.Windows.Forms.Panel
    $watch_strip.Dock = 'Top'
    $watch_strip.Height = 38
    $watch_strip.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $label_wname = New-Object System.Windows.Forms.Label
    $label_wname.Text = '名称'
    $label_wname.Location = New-Object System.Drawing.Point(10, 12)
    $label_wname.AutoSize = $true
    $label_wname.ForeColor = $dim_fg
    $watch_strip.Controls.Add($label_wname)

    $script:modbus_watch_name = New-Object System.Windows.Forms.TextBox
    $script:modbus_watch_name.Location = New-Object System.Drawing.Point(46, 7)
    $script:modbus_watch_name.Size = New-Object System.Drawing.Size(110, 24)
    $script:modbus_watch_name.BackColor = $input_bg
    $script:modbus_watch_name.ForeColor = $text_fg
    $script:modbus_watch_name.BorderStyle = 'FixedSingle'
    $watch_strip.Controls.Add($script:modbus_watch_name)

    $button_watch_add = New-Object System.Windows.Forms.Button
    $button_watch_add.Text = '← 从事务添加'
    $button_watch_add.Location = New-Object System.Drawing.Point(168, 6)
    $button_watch_add.Size = New-Object System.Drawing.Size(110, 26)
    $button_watch_add.FlatStyle = 'Flat'
    $button_watch_add.BackColor = [System.Drawing.Color]::FromArgb(14, 99, 156)
    $button_watch_add.ForeColor = [System.Drawing.Color]::White
    $watch_strip.Controls.Add($button_watch_add)

    $button_watch_del = New-Object System.Windows.Forms.Button
    $button_watch_del.Text = '删除选中'
    $button_watch_del.Location = New-Object System.Drawing.Point(284, 6)
    $button_watch_del.Size = New-Object System.Drawing.Size(70, 26)
    $button_watch_del.FlatStyle = 'Flat'
    $button_watch_del.BackColor = $input_bg
    $button_watch_del.ForeColor = $text_fg
    $watch_strip.Controls.Add($button_watch_del)

    $button_watch_clear = New-Object System.Windows.Forms.Button
    $button_watch_clear.Text = '清空'
    $button_watch_clear.Location = New-Object System.Drawing.Point(358, 6)
    $button_watch_clear.Size = New-Object System.Drawing.Size(52, 26)
    $button_watch_clear.FlatStyle = 'Flat'
    $button_watch_clear.BackColor = $input_bg
    $button_watch_clear.ForeColor = $text_fg
    $watch_strip.Controls.Add($button_watch_clear)

    $script:modbus_watch_check = New-Object System.Windows.Forms.CheckBox
    $script:modbus_watch_check.Text = '监视'
    $script:modbus_watch_check.Location = New-Object System.Drawing.Point(422, 10)
    $script:modbus_watch_check.AutoSize = $true
    $script:modbus_watch_check.ForeColor = $dim_fg
    $watch_strip.Controls.Add($script:modbus_watch_check)

    $script:modbus_watch_period = New-Object System.Windows.Forms.ComboBox
    $script:modbus_watch_period.Location = New-Object System.Drawing.Point(482, 7)
    $script:modbus_watch_period.Size = New-Object System.Drawing.Size(64, 24)
    $script:modbus_watch_period.DropDownStyle = 'DropDownList'
    [void]$script:modbus_watch_period.Items.AddRange(@('500ms', '1s', '2s'))
    $script:modbus_watch_period.SelectedIndex = 0
    $script:modbus_watch_period.BackColor = $input_bg
    $script:modbus_watch_period.ForeColor = $text_fg
    $script:modbus_watch_period.FlatStyle = 'Flat'
    $watch_strip.Controls.Add($script:modbus_watch_period)

    # 监视表：名称 / 键 / 值 / 格式 / 状态（值列按涨跌闪色）
    $script:modbus_watch_grid = New-Object System.Windows.Forms.DataGridView
    $script:modbus_watch_grid.Dock = 'Fill'
    $script:modbus_watch_grid.ReadOnly = $true
    $script:modbus_watch_grid.AllowUserToAddRows = $false
    $script:modbus_watch_grid.AllowUserToDeleteRows = $false
    $script:modbus_watch_grid.RowHeadersVisible = $false
    $script:modbus_watch_grid.SelectionMode = 'FullRowSelect'
    $script:modbus_watch_grid.MultiSelect = $false
    $script:modbus_watch_grid.AutoSizeColumnsMode = 'Fill'
    $script:modbus_watch_grid.BackgroundColor = [System.Drawing.Color]::FromArgb(16, 20, 28)
    $script:modbus_watch_grid.GridColor = [System.Drawing.Color]::FromArgb(45, 52, 66)
    $script:modbus_watch_grid.BorderStyle = 'None'
    $script:modbus_watch_grid.EnableHeadersVisualStyles = $false
    $script:modbus_watch_grid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $script:modbus_watch_grid.ColumnHeadersDefaultCellStyle.ForeColor = $dim_fg
    $script:modbus_watch_grid.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(16, 20, 28)
    $script:modbus_watch_grid.DefaultCellStyle.ForeColor = $text_fg
    $script:modbus_watch_grid.DefaultCellStyle.SelectionBackColor = [System.Drawing.Color]::FromArgb(38, 64, 92)
    $script:modbus_watch_grid.DefaultCellStyle.SelectionForeColor = $text_fg
    [void]$script:modbus_watch_grid.Columns.Add('name', '名称')
    [void]$script:modbus_watch_grid.Columns.Add('key', '键')
    [void]$script:modbus_watch_grid.Columns.Add('value', '值')
    [void]$script:modbus_watch_grid.Columns.Add('decode', '格式')
    [void]$script:modbus_watch_grid.Columns.Add('status', '状态')
    $tab_watch.Controls.Add($script:modbus_watch_grid)
    $tab_watch.Controls.Add($watch_strip)

    $form.Controls.Add($script:modbus_tab_ctrl)
    $form.Controls.Add($toolbar)

    $button_run.Add_Click({ run_modbus_ui_transaction })
    $script:modbus_check_poll.Add_CheckedChanged({
        if ($script:modbus_check_poll.Checked) {
            $period_ms = 500
            switch ([string]$script:modbus_combo_period.SelectedItem) {
                '1s' { $period_ms = 1000 }
                '2s' { $period_ms = 2000 }
                default { $period_ms = 500 }
            }
            if ($script:modbus_poll_timer) {
                $script:modbus_poll_timer.Interval = $period_ms
                $script:modbus_poll_timer.Start()
            }
        }
        else {
            if ($script:modbus_poll_timer) {
                $script:modbus_poll_timer.Stop()
            }
        }
    })

    $button_watch_add.Add_Click({ add_modbus_watch_from_ui })
    $button_watch_del.Add_Click({ remove_selected_modbus_watch })
    $button_watch_clear.Add_Click({ clear_modbus_watch })

    $script:modbus_watch_check.Add_CheckedChanged({
        if ($script:modbus_watch_check.Checked) {
            $watch_ms = 500
            switch ([string]$script:modbus_watch_period.SelectedItem) {
                '1s' { $watch_ms = 1000 }
                '2s' { $watch_ms = 2000 }
                default { $watch_ms = 500 }
            }
            if ($script:modbus_watch_timer) {
                $script:modbus_watch_timer.Interval = $watch_ms
                $script:modbus_watch_timer.Start()
            }
        }
        else {
            if ($script:modbus_watch_timer) {
                $script:modbus_watch_timer.Stop()
            }
        }
    })

    $script:modbus_watch_period.Add_SelectedIndexChanged({
        if ($script:modbus_watch_check.Checked -and $script:modbus_watch_timer) {
            $watch_ms = 500
            switch ([string]$script:modbus_watch_period.SelectedItem) {
                '1s' { $watch_ms = 1000 }
                '2s' { $watch_ms = 2000 }
                default { $watch_ms = 500 }
            }
            $script:modbus_watch_timer.Interval = $watch_ms
        }
    })

    # 双击监视行 = 回填到静态事务区编辑（切换交互的桥）
    $script:modbus_watch_grid.Add_CellDoubleClick({
        param($sender, $e)
        if ($e.RowIndex -lt 0) { return }
        $row = $script:modbus_watch_grid.Rows[$e.RowIndex]
        $item = $row.Tag
        if (-not $item) { return }
        $script:modbus_text_slave.Text = [string]$item.Slave
        if ($item.Fc -ge 1 -and $item.Fc -le 4) {
            $script:modbus_combo_fc.SelectedIndex = $item.Fc - 1
        }
        $script:modbus_text_start.Text = [string]$item.Start
        $script:modbus_text_count.Text = [string]$item.Count
        $script:modbus_text_value.Text = ''
        $script:modbus_watch_name.Text = $item.Name
        $script:modbus_combo_decode.SelectedItem = $item.Decode
        $script:modbus_tab_ctrl.SelectedIndex = 0
    })

    $script:modbus_poll_timer = New-Object System.Windows.Forms.Timer
    $script:modbus_poll_timer.Interval = 500
    $script:modbus_poll_timer.Add_Tick({ run_modbus_ui_transaction })

    $script:modbus_watch_timer = New-Object System.Windows.Forms.Timer
    $script:modbus_watch_timer.Interval = 500
    $script:modbus_watch_timer.Add_Tick({ run_modbus_watch_cycle })

    $form.Add_FormClosed({
        foreach ($t in @($script:modbus_poll_timer, $script:modbus_watch_timer)) {
            if ($t) {
                try {
                    $t.Stop()
                    $t.Dispose()
                }
                catch {
                }
            }
        }
        $script:modbus_poll_timer = $null
        $script:modbus_watch_timer = $null
        $script:modbus_watch_rows.Clear()
        $script:modbus_watch_items.Clear()
        $script:modbus_form = $null
    })

    $script:modbus_form = $form
    update_modbus_status
    append_modbus_result -text 'Modbus RTU 调试台就绪。设备侧为从站；主窗口需已连接串口。'
    append_modbus_result -text '「实时监视」页可把读地址加入 Watch 表，按关键(从站:FC:地址)合并轮询、涨绿跌红；双击行回填到事务区。'
    $form.Show()
}

Register-WavePlugin -Name 'Modbus' -Description 'Modbus RTU 主站：读/写线圈与寄存器，CRC16 校验、轮询、多格式解码，事务回写主日志' -OpenPanel ${function:show_modbus_panel}
