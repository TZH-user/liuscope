# ui\theme.ps1 — 主题：调色板应用、按钮样式、滚动条与标题栏深色化、主题审计
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function enable_dark_title_bar {
    param(
        [System.Windows.Forms.Form]$form,
        [bool]$enabled = $true
    )
    try {
        if ($form -and -not $form.IsDisposed) {
            [DwmDark]::EnableDarkTitleBar($form.Handle, $enabled)
        }
    }
    catch {
    }
}

function style_main_button {
    # 主窗体按钮统一扁平款，样式全部取自 $script:ui（主题感知）。
    # -accent = 主操作（实底强调色）。
    param([System.Windows.Forms.Button]$button, [switch]$accent)

    if (-not $button) {
        return
    }

    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderSize = 1
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    if ($accent) {
        $button.BackColor = $script:ui.Accent
        $button.FlatAppearance.BorderColor = $script:ui.Accent
        $button.ForeColor = [System.Drawing.Color]::White
        $button.FlatAppearance.MouseOverBackColor = $script:ui.AccentHover
        $button.FlatAppearance.MouseDownBackColor = $script:ui.AccentDown
    }
    else {
        $button.BackColor = $script:ui.BtnFace
        $button.FlatAppearance.BorderColor = $script:ui.InputEdge
        $button.ForeColor = $script:ui.TextMain
        $button.FlatAppearance.MouseOverBackColor = $script:ui.BtnHover
        $button.FlatAppearance.MouseDownBackColor = $script:ui.BtnDown
    }
}

function set_main_button_enabled {
    # 主窗体【不用】Enabled=False。实测（_probe_paint.ps1 / logs/probe_theme.png）：
    # WinForms 禁用态 Flat 按钮文字恒为近黑，且与 BackColor 无关——
    # #2A3244 底 0.32:1，底色提亮到 #384054 仍只有 0.33:1。
    # 唯一可读方案是保持 Enabled=$true，自己套静音配色（#262D3A/#969EAC = 3.54:1），
    # 再由调用方按状态位做点击守卫。
    param(
        [System.Windows.Forms.Button]$button,
        [bool]$enabled,
        [switch]$accent
    )

    if (-not $button) {
        return
    }

    # 连 FlatStyle 一起归位：禁用分支不走 style_main_button，
    # 否则按钮会保留系统 Standard 边框，和周围扁平款不一致
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderSize = 1
    $button.Enabled = $true
    if ($enabled) {
        style_main_button -button $button -accent:$accent
    }
    elseif ($accent) {
        # accent 静音态用 TextMain 而非 TextDim：深色下 AccentMutedBg(#263442)
        # 配 TextDim(#9D9D9D) 只有 2.85:1，换 TextMain(#D4D4D4) 是 3.85:1
        $button.BackColor = $script:ui.AccentMutedBg
        $button.ForeColor = $script:ui.TextMain
        $button.FlatAppearance.BorderColor = $script:ui.AccentMutedEdg
        $button.FlatAppearance.MouseOverBackColor = $script:ui.AccentMutedBg
        $button.FlatAppearance.MouseDownBackColor = $script:ui.AccentMutedBg
    }
    else {
        $button.BackColor = $script:ui.BtnMutedBg
        $button.ForeColor = $script:ui.TextMuted
        $button.FlatAppearance.BorderColor = $script:ui.BtnMutedEdge
        $button.FlatAppearance.MouseOverBackColor = $script:ui.BtnMutedBg
        $button.FlatAppearance.MouseDownBackColor = $script:ui.BtnMutedBg
    }
}

function sync_connect_buttons {
    # 连接/断开互斥：始终保持两者 Enabled=$true（原因见 set_main_button_enabled），
    # 只切配色，点击守卫在各自 Add_Click 里按 $script:connected 判断。
    # 控件尚未创建时直接返回：StrictMode 下访问未定义变量会抛，
    # 用 Test-Path 而不是 `if ($button_connect)` 来做这个判断。
    if (-not (Test-Path -Path 'Variable:script:button_connect')) {
        return
    }

    $is_connected = ($script:connected -eq $true)
    set_main_button_enabled -button $button_connect -enabled (-not $is_connected) -accent
    set_main_button_enabled -button $button_disconnect -enabled $is_connected
}

function get_all_child_controls {
    # 递归收集窗体下所有控件。手维护控件清单必然漏项——上一版就是只挂了
    # 两个下拉框，结果其余下拉框的下拉箭头方块、全部复选框方框仍是系统浅色。
    param([System.Windows.Forms.Control]$root)

    $list = New-Object 'System.Collections.Generic.List[System.Windows.Forms.Control]'
    foreach ($child in $root.Controls) {
        [void]$list.Add($child)
        foreach ($sub in (get_all_child_controls -root $child)) {
            [void]$list.Add($sub)
        }
    }
    return $list
}

function apply_dark_scrollbars {
    # 深色滚动条 + 深色下拉箭头 + 深色复选框：这三种都是系统用主题画的，
    # 不吃 BackColor，唯一可控的办法是给控件 Handle 挂 uxtheme 的
    # DarkMode_Explorer（Win10 1809+ / Win11 官方暗色控件主题）。
    # 必须在句柄建好之后调，换主题时重刷一遍。
    #
    # 只信 SetWindowTheme 的 S_OK 计数，不要用 GetWindowTheme 读回去判断：
    # 标准控件的主题句柄是绘制时才惰性创建的，读回常年是 0。
    param([System.Windows.Forms.Form]$target_form)

    $script:scrollbar_darkened = 0
    try {
        if (-not $target_form -or $target_form.IsDisposed) {
            return
        }

        $targets = New-Object 'System.Collections.Generic.List[System.Windows.Forms.Control]'
        foreach ($ctrl in (get_all_child_controls -root $target_form)) {
            # ComboBox：下拉箭头方块；CheckBox：勾选框方块；TextBoxBase：滚动条
            if ($ctrl -is [System.Windows.Forms.ComboBox] -or
                    $ctrl -is [System.Windows.Forms.CheckBox] -or
                    $ctrl -is [System.Windows.Forms.TextBoxBase]) {
                if (-not $ctrl.IsDisposed -and $ctrl.IsHandleCreated) {
                    [void]$targets.Add($ctrl)
                }
            }
        }

        foreach ($ctrl in $targets) {
            if ([UxTheme]::ApplyDark($ctrl.Handle)) {
                $script:scrollbar_darkened++
            }
        }

        # 兜底：万一某些控件用的是独立 ScrollBar 子窗口
        if ($target_form.IsHandleCreated) {
            $script:scrollbar_darkened += [UxTheme]::ApplyDarkScrollbars($target_form.Handle)
        }
    }
    catch {
        $script:scrollbar_darkened = -1
    }
}

function recolor_log_text {
    # RichTextBox 里已落色的文字不会跟着 ForeColor 变体（逐字符 SelectionColor 优先），
    # 切主题后旧文字会变成「浅色字压深底」或反过来，必须整段重刷一次。
    param([System.Windows.Forms.TextBoxBase]$text_box, [System.Drawing.Color]$color)

    if (-not $text_box -or $text_box.TextLength -le 0) {
        return
    }

    $rich_box = $text_box -as [System.Windows.Forms.RichTextBox]
    if (-not $rich_box) {
        return
    }

    $rich_box.SelectionStart = 0
    $rich_box.SelectionLength = $rich_box.TextLength
    $rich_box.SelectionColor = $color
    $rich_box.SelectionStart = $rich_box.TextLength
    $rich_box.SelectionLength = 0
}

function apply_theme {
    param(
        [string]$theme_name
    )

    $is_dark = $theme_name -ne 'Light'
    $script:ui_is_dark = $is_dark
    if ($is_dark) {
        $script:ui = $script:ui_palettes['Dark']
    }
    else {
        $script:ui = $script:ui_palettes['Light']
    }
    $ui = $script:ui

    # 窗体与分区（三级背景：间隙最亮 / 面板居中 / 日志区最深）
    $form.BackColor = $ui.FormBg
    $form.ForeColor = $ui.TextMain
    $panel_top.BackColor = $ui.PanelBg
    $panel_bottom.BackColor = $ui.PanelBg2
    # 底部状态条：整条实色，与上下区域形成明确的横向边界（构图上的"封口"）
    $panel_status.BackColor = $ui.StatusBg
    $panel_status.ForeColor = $ui.StatusFg
    $split_logs.BackColor = $ui.SplitterBg
    $split_logs.Panel1.BackColor = $ui.LogBgRx
    $split_logs.Panel2.BackColor = $ui.LogBgTx

    # 主标签：TextMain；提示/路径类：TextDim
    foreach ($control in @($label_port, $label_baud, $label_data_bits, $label_parity,
            $label_stop_bits, $label_flow_control, $label_theme, $label_rx_format,
            $label_tx_format, $label_status_title, $label_send, $label_newline, $label_actions)) {
        $control.ForeColor = $ui.TextMain
        $control.BackColor = [System.Drawing.Color]::Transparent
    }
    foreach ($control in @($label_log_path, $label_hint)) {
        $control.ForeColor = $ui.TextDim
        $control.BackColor = [System.Drawing.Color]::Transparent
    }
    # 状态条上的文字：走状态条自己的前景色，不跟面板主色
    foreach ($control in @($label_status_title, $label_status)) {
        $control.ForeColor = $ui.StatusFg
        $control.BackColor = [System.Drawing.Color]::Transparent
    }
    $label_log_path.ForeColor = $ui.StatusFg
    $label_log_path.BackColor = [System.Drawing.Color]::Transparent
    # 状态条上的复选框底色必须不透明（自绘类在透明底上会重影）
    $checkbox_autolink.BackColor = $ui.StatusBg
    $checkbox_autolink.ForeColor = $ui.StatusFg
    $checkbox_autolink.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $checkbox_autolink.UseDark = $is_dark
    $checkbox_autolink.BoxBorder = $ui.StatusFg
    $checkbox_autolink.MarkColor = $ui.StatusFg

    # 日志区标题条：细字 + 弱化色，底色与日志区同色（不再做成额外色块）
    $label_rx.ForeColor = $ui.TextDim
    $label_rx.BackColor = $ui.LogBgRx
    $label_tx.ForeColor = $ui.TextDim
    $label_tx.BackColor = $ui.LogBgTx

    # 区域分隔线：三级底色之外再给 1px 实线，纯色差在低对比屏上会被吃掉
    $sep_top.BackColor = $ui.Separator
    $sep_bottom.BackColor = $ui.Separator

    # 下拉框：深色下拉框直接设 BackColor/ForeColor 即可出可读白字，
    # 无需 OwnerDraw（探针已验证）。但下拉箭头方块是系统画的，只吃 BackColor
    # 挡不住，必须靠 DarkComboBox 自绘重绘（UseDark=深色时开，浅色关）。
    foreach ($combo in @($combobox_port, $combobox_baud, $combobox_data_bits, $combobox_parity,
            $combobox_stop_bits, $combobox_flow_control, $combobox_theme,
            $combobox_rx_format, $combobox_tx_format, $combobox_reset_mode, $combobox_newline)) {
        $combo.BackColor = $ui.InputBg
        $combo.ForeColor = $ui.TextMain
        $combo.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $combo.UseDark = $is_dark
        $combo.ArrowBack = $ui.InputBg
        $combo.ArrowFore = $ui.TextMain
        $combo.BorderColor = $ui.InputEdge
        # 可编辑下拉框（波特率）的子 EDIT 控件上色：深色时走 WM_CTLCOLOREDIT 画刷
        $combo.EnableDarkEdit($is_dark, $ui.InputBg, $ui.TextMain)
    }

    # 可编辑文本框
    foreach ($box in @($textbox_send, $textbox_flash_cmd)) {
        $box.BackColor = $ui.InputBg
        $box.ForeColor = $ui.TextMain
    }

    # 复选框：勾选框方块系统绘制，只能靠 DarkCheckBox 自绘（CheckBox 无 DrawMode）。
    # 坑：BackColor 必须是【不透明】的面板色，不能用 Transparent。透明底色下 WinForms
    # 会让原生控件把文字也画一遍，和 OnPaint 自绘文字叠成重影（screen 抓屏实测到糊字）。
    # 因此按所在面板给不同底色：上半（panel_top）/ 下半（panel_bottom）。
    # 注意：$checkbox_autolink 已迁到底部状态条，底色/字色由上面的状态条分支负责，
    # 这里不能再用 PanelBg 覆盖（覆盖后状态条上会出现一块异色方块）
    foreach ($box in @($checkbox_dtr, $checkbox_rts, $checkbox_auto_save)) {
        $box.BackColor = $ui.PanelBg
        $box.ForeColor = $ui.TextMain
        $box.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $box.UseDark = $is_dark
        $box.BoxBorder = $ui.TextDim
        $box.MarkColor = $ui.TextMain
    }
    foreach ($box in @($checkbox_append_newline, $checkbox_clear_after_send)) {
        $box.BackColor = $ui.PanelBg2
        $box.ForeColor = $ui.TextMain
        $box.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $box.UseDark = $is_dark
        $box.BoxBorder = $ui.TextDim
        $box.MarkColor = $ui.TextMain
    }

    # 日志文本框
    $textbox_rx.BackColor = $ui.LogBgRx
    $textbox_rx.ForeColor = $ui.LogTextRx
    $textbox_tx.BackColor = $ui.LogBgTx
    $textbox_tx.ForeColor = $ui.LogTextTx
    recolor_log_text -text_box $textbox_rx -color $ui.LogTextRx
    recolor_log_text -text_box $textbox_tx -color $ui.LogTextTx

    # 滚动条：uxtheme 暗色滚动条在浅色系统上无效，改走「去主题 + WM_CTLCOLORSCROLLBAR」，
    # 把滑道刷成对应底色，压掉深色下刺眼的纯白条；保留原生滚动/命中测试。
    $textbox_rx.EnableDarkScroll($is_dark, $ui.LogBgRx)
    $textbox_tx.EnableDarkScroll($is_dark, $ui.LogBgTx)
    $textbox_send.EnableDarkScroll($is_dark, $ui.InputBg)

    # 按钮：全部统一扁平款，只有「连接」是 accent
    foreach ($button in @($button_refresh, $button_wave, $button_clear, $button_save,
            $button_actions_toggle, $button_reset, $button_flash, $button_hit_clear, $button_send,
            $button_find_prev, $button_find_next, $button_find_close)) {
        style_main_button -button $button
    }
    sync_connect_buttons

    # 查找栏（Ctrl+F 呼出；控件在主窗体构建期创建，这里做主题切换重上色）
    $script:find_panel.BackColor = $ui.PanelBg
    $script:find_box.BackColor = $ui.InputBg
    $script:find_box.ForeColor = $ui.TextMain
    $label_find.ForeColor = $ui.TextDim
    $script:find_label_count.ForeColor = $ui.TextDim
    # 旧高亮底色是按旧主题铺的，切主题后清掉（下次查找重铺）
    try { clear_find_highlights } catch {}

    # 标题栏跟随主题（Light 下要显式关掉，否则窗口边框还是深色）
    enable_dark_title_bar -form $form -enabled $is_dark

    # 滚动条独立于 BackColor，深色下必须单独挂 uxtheme 暗色主题，
    # 否则 RX/TX/发送框边会留一条系统纯白滚动条。
    # 注意：这里只信 SetWindowTheme 的 S_OK 计数，不要用 GetWindowTheme 读回
    # 去判断——标准 EDIT 控件的 HTHEME 是绘制时才惰性创建的，读回常年是 0。
    $script:scrollbar_darkened = 0
    # uxtheme DarkMode_Explorer 在浅色系统上无效，且会抵消自绘滚动条的 theme-strip，故不调用
}

function write_theme_audit {
    # 截图只能证明「画得像什么」，证明不了「控件属性是什么」。
    # ComboBox / 标题栏在 DrawToBitmap 下会失真，所以把控件真实属性也落一份盘，
    # 主题回归时对照 PNG 与本文件一起看。
    param([string]$path)

    $rows = @()
    $rows += ('# theme audit  {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    $rows += ('# active palette = {0}' -f $(if ($script:ui -eq $script:ui_palettes['Light']) { 'Light' } else { 'Dark' }))
    # 深色控件走自绘（DarkComboBox/DarkCheckBox/EnableDarkScroll），不再挂 uxtheme，
    # scrollbar_darkened 恒为 0 是【预期】；该行仅作历史兼容保留。
    $rows += ('# dark controls: owner-draw (uxtheme SetWindowTheme intentionally unused, count={0})' -f $script:scrollbar_darkened)

    $targets = @(
        @{ n = 'form'; c = $form },
        @{ n = 'panel_top'; c = $panel_top },
        @{ n = 'panel_bottom'; c = $panel_bottom },
        @{ n = 'split_logs'; c = $split_logs },
        @{ n = 'label_rx'; c = $label_rx },
        @{ n = 'label_tx'; c = $label_tx },
        @{ n = 'label_port'; c = $label_port },
        @{ n = 'label_hint'; c = $label_hint },
        @{ n = 'label_status'; c = $label_status },
        @{ n = 'textbox_rx'; c = $textbox_rx },
        @{ n = 'textbox_tx'; c = $textbox_tx },
        @{ n = 'textbox_send'; c = $textbox_send },
        @{ n = 'combobox_port'; c = $combobox_port },
        @{ n = 'combobox_baud'; c = $combobox_baud },
        @{ n = 'combobox_data_bits'; c = $combobox_data_bits },
        @{ n = 'combobox_parity'; c = $combobox_parity },
        @{ n = 'combobox_stop_bits'; c = $combobox_stop_bits },
        @{ n = 'combobox_flow_control'; c = $combobox_flow_control },
        @{ n = 'combobox_theme'; c = $combobox_theme },
        @{ n = 'combobox_rx_format'; c = $combobox_rx_format },
        @{ n = 'combobox_tx_format'; c = $combobox_tx_format },
        @{ n = 'combobox_newline'; c = $combobox_newline },
        @{ n = 'checkbox_dtr'; c = $checkbox_dtr },
        @{ n = 'checkbox_auto_save'; c = $checkbox_auto_save },
        @{ n = 'checkbox_append_newline'; c = $checkbox_append_newline },
        @{ n = 'button_connect'; c = $button_connect },
        @{ n = 'button_disconnect'; c = $button_disconnect },
        @{ n = 'button_clear'; c = $button_clear },
        @{ n = 'button_send'; c = $button_send },
        @{ n = 'button_actions_toggle'; c = $button_actions_toggle }
    )

    foreach ($t in $targets) {
        $ctrl = $t.c
        if (-not $ctrl) {
            $rows += ('{0,-24} <null>' -f $t.n)
            continue
        }
        # FlatStyle / Enabled 并非所有控件都有（Form、Panel、SplitContainer 就没有），
        # 而 Set-StrictMode -Version Latest 下读不存在的属性会直接抛，
        # 所以只能按实际存在的属性来拼这一行。
        $props = $ctrl.PSObject.Properties
        # BackColor 可能是 Transparent（A=0，RGB 恒为 FFFFFF），只打 RGB 会误报
        # 「白底标签」——必须带 alpha 通道，透明时直接标注 transparent。
        $format_color = {
            param([System.Drawing.Color]$color)
            if ($color.A -eq 0) { return 'transparent' }
            return ('#{0:X2}{1:X2}{2:X2}' -f $color.R, $color.G, $color.B)
        }
        $bits = @(
            ('back=' + (& $format_color $ctrl.BackColor)),
            ('fore=' + (& $format_color $ctrl.ForeColor))
        )
        # 主题断言：当前为深色主题时，任何不透明底色亮度 >200 即视为回归，
        # 直接写进审计行尾（人工/脚本 grep 'THEME_FAIL' 即可拦截）。
        if ($script:ui_is_dark -and $ctrl.BackColor.A -eq 255) {
            $lum = (0.299 * $ctrl.BackColor.R + 0.587 * $ctrl.BackColor.G + 0.114 * $ctrl.BackColor.B)
            if ($lum -gt 200) {
                $bits += ('THEME_FAIL bright_back lum={0:N0}' -f $lum)
            }
        }
        if ($props['FlatStyle']) { $bits += ('flat=' + $ctrl.FlatStyle) }
        if ($props['Enabled']) { $bits += ('enabled=' + $ctrl.Enabled) }
        # DrawToBitmap 不渲染 RichTextBox/TextBox 的文字（渲染出来是纯色块），
        # 光看截图会误判成「日志区没内容」，所以把文本长度也记下来
        if ($props['TextLength']) { $bits += ('text_len=' + $ctrl.TextLength) }
        $rows += ('{0,-24} {1}' -f $t.n, ($bits -join ' '))
    }

    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Path $dir -Force)
    }
    [System.IO.File]::WriteAllLines($path, $rows, [System.Text.UTF8Encoding]::new($false))
}

