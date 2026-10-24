# ui\main_form.ps1 — 主窗体行为：板卡操作行收展、会话保存、端口下拉同步
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function update_port_combobox {
    param([hashtable]$device_map)

    $port_names = @()
    try {
        $port_names = @([System.IO.Ports.SerialPort]::GetPortNames() |
            Sort-Object { [int]([regex]::Match($_, '\d+').Value) } |
            Select-Object -Unique)
    }
    catch {
    }

    $items = New-Object System.Collections.Generic.List[string]
    foreach ($name in $port_names) {
        $desc = ''
        if ($device_map -and $device_map.ContainsKey($name)) {
            $desc = [string]$device_map[$name]
        }
        $items.Add((get_port_display_text -port_name $name -description $desc))
    }

    # @() 包装：StrictMode 下空结果被展开为 $null，.Count 会抛异常
    $new_ports = @(detect_new_ports -current_names $port_names -previous_names $script:known_ports -first_done $script:port_probe_first_done)
    $script:port_probe_first_done = $true
    $script:known_ports = $port_names

    # 重建下拉列表（带描述），尽量保留当前选择
    $current_port = get_port_name_from_display -text $combobox_port.Text
    $ch340_index = -1
    for ($i = 0; $i -lt $items.Count; $i++) {
        if (test_is_ch340_description -description $items[$i]) {
            $ch340_index = $i
            break
        }
    }
    $combobox_port.Items.Clear()
    foreach ($item in $items) {
        [void]$combobox_port.Items.Add($item)
    }

    $selected_index = -1
    if ($current_port) {
        for ($i = 0; $i -lt $items.Count; $i++) {
            if ((get_port_name_from_display -text $items[$i]) -eq $current_port) {
                $selected_index = $i
                break
            }
        }
    }
    if ($selected_index -lt 0 -and $items.Count -gt 0) {
        # 用户没有明确选择：优先恢复上次使用的端口，其次 CH340（鲁班猫调试口），否则第一项
        if ($script:saved_port_name) {
            for ($i = 0; $i -lt $items.Count; $i++) {
                if ((get_port_name_from_display -text $items[$i]) -eq $script:saved_port_name) {
                    $selected_index = $i
                    break
                }
            }
        }
        if ($selected_index -lt 0 -and $ch340_index -ge 0) {
            $selected_index = $ch340_index
        }
        if ($selected_index -lt 0) {
            $selected_index = 0
        }
    }
    if ($selected_index -ge 0) {
        $combobox_port.SelectedIndex = $selected_index
    }
    else {
        $combobox_port.Text = ''
    }

    if ($new_ports.Count -gt 0) {
        # 新设备出现：无论自动连接开关，先把选择切过去（CH340 优先）
        $auto_target = pick_auto_connect_port -new_ports $new_ports -device_map $device_map
        if (-not [string]::IsNullOrWhiteSpace($auto_target)) {
            for ($i = 0; $i -lt $items.Count; $i++) {
                if ((get_port_name_from_display -text $items[$i]) -eq $auto_target) {
                    $combobox_port.SelectedIndex = $i
                    break
                }
            }
        }
        if ($checkbox_autolink.Checked -and -not $script:connected) {
            handle_auto_connect -new_ports $new_ports -device_map $device_map
        }
    }
}

function save_gui_session {
    # 关闭时保存"上次使用"参数：波特率 / 串口 / 收发格式（auto_link 由勾选事件即时保存）
    try {
        if ($combobox_baud) {
            $baud_text = $combobox_baud.Text.Trim()
            if ($baud_text -match '^\d+$') {
                save_gui_setting -key 'baud' -value $baud_text
            }
        }
        if ($combobox_port) {
            $port_text = get_port_name_from_display -text $combobox_port.Text
            if (-not [string]::IsNullOrWhiteSpace($port_text)) {
                save_gui_setting -key 'port' -value $port_text
            }
        }
        if ($combobox_rx_format) {
            save_gui_setting -key 'rx_format' -value ([string]$combobox_rx_format.SelectedItem)
        }
        if ($combobox_tx_format) {
            save_gui_setting -key 'tx_format' -value ([string]$combobox_tx_format.SelectedItem)
        }
    }
    catch {
    }
}

function set_actions_expanded {
    param([bool]$expanded)

    $script:actions_expanded = $expanded
    foreach ($control in @($combobox_reset_mode, $button_reset, $textbox_flash_cmd, $button_flash, $button_hit_clear)) {
        $control.Visible = $expanded
    }
    $button_actions_toggle.Text = $(if ($expanded) { '−' } else { '+' })
    if ($expanded) {
        # 展开状态：按钮在第三行行首（y=86），标签随后；面板高 124
        $panel_top.Height = 124
        $button_actions_toggle.Location = New-Object System.Drawing.Point(10, 86)
        $label_actions.Location = New-Object System.Drawing.Point(46, 92)
        $label_actions.Visible = $true
    }
    else {
        # 收起状态：第三行隐藏（面板高 82），按钮与提示标签移到第二行右端，
        # 否则按钮会被面板裁掉找不到
        $panel_top.Height = 82
        $button_actions_toggle.Location = New-Object System.Drawing.Point(1196, 46)
        $label_actions.Location = New-Object System.Drawing.Point(1126, 52)
        $label_actions.Visible = $true
    }

    # 收起状态下板卡操作那排控件是隐藏的，句柄根本没创建，
    # 展开的瞬间才建出来——所以要在这里补挂一次暗色控件主题。
    if ($script:ui_is_dark) {
        [System.Windows.Forms.Application]::DoEvents()
        # 板卡操作行的控件同样是自绘类，展开建出句柄后重绘一次即可（不再挂 uxtheme）
        if (-not $form.IsDisposed) { $form.Invalidate() }
    }
}

