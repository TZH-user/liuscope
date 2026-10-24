# ui\wave_form.ps1 — 波形窗口：构建、渲染、导出、插件装载、模拟数据源
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function get_wave_channel_color {
    param([int]$index)

    $palette = @(
        @(0, 200, 255), @(255, 110, 110), @(120, 255, 140), @(255, 220, 0),
        @(255, 130, 255), @(170, 140, 255), @(255, 160, 60), @(0, 255, 200),
        @(90, 160, 255), @(255, 105, 180), @(180, 255, 60), @(255, 250, 150),
        @(0, 150, 255), @(200, 120, 255), @(120, 220, 120), @(255, 200, 120)
    )
    $color = $palette[$index % $palette.Count]
    [System.Drawing.Color]::FromArgb($color[0], $color[1], $color[2])
}

function show_channel_rename_dialog {
    param([int]$index)

    if ($index -lt 0 -or $index -ge $script:wave_channel_checks.Count) {
        return
    }

    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = ('重命名通道 {0}' -f $index)
    $dlg.StartPosition = 'CenterParent'
    $dlg.Size = New-Object System.Drawing.Size(360, 160)
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false
    $dlg.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $dlg.Font = new_font -size 9

    $tip_label = New-Object System.Windows.Forms.Label
    $tip_label.Text = ('通道 {0} 显示名称（留空恢复 CH{0}）：' -f $index)
    $tip_label.Location = New-Object System.Drawing.Point(14, 12)
    $tip_label.AutoSize = $true
    $tip_label.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $dlg.Controls.Add($tip_label)

    $name_box = New-Object System.Windows.Forms.TextBox
    $name_box.Text = if ($script:wave_channel_checks[$index].Text -ne ('CH{0}' -f $index)) { [string]$script:wave_channel_checks[$index].Text } else { '' }
    $name_box.Location = New-Object System.Drawing.Point(14, 38)
    $name_box.Size = New-Object System.Drawing.Size(310, 26)
    $dlg.Controls.Add($name_box)

    $ok_button = New-Object System.Windows.Forms.Button
    $ok_button.Text = '确定'
    $ok_button.Location = New-Object System.Drawing.Point(156, 78)
    $ok_button.Size = New-Object System.Drawing.Size(80, 28)
    $ok_button.DialogResult = [System.Windows.Forms.DialogResult]::OK
    style_wave_button -button $ok_button
    $dlg.Controls.Add($ok_button)

    $cancel_button = New-Object System.Windows.Forms.Button
    $cancel_button.Text = '取消'
    $cancel_button.Location = New-Object System.Drawing.Point(244, 78)
    $cancel_button.Size = New-Object System.Drawing.Size(80, 28)
    $cancel_button.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    style_wave_button -button $cancel_button
    $dlg.Controls.Add($cancel_button)

    $dlg.AcceptButton = $ok_button
    $dlg.CancelButton = $cancel_button

    if ($dlg.ShowDialog($script:wave_form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $new_name = $name_box.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($new_name)) {
            # 留空恢复默认 CHn
            while ($script:wave_channel_names.Count -le $index) {
                [void]$script:wave_channel_names.Add('')
            }
            $script:wave_channel_names[$index] = ''
            $script:wave_channel_checks[$index].Text = ('CH{0}' -f $index)
            save_gui_setting -key 'channel_names' -value (($script:wave_channel_names | ForEach-Object { $_ }) -join ',')
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
        else {
            set_wave_channel_name -index $index -name $new_name
        }
    }
    $dlg.Dispose()
}

function ensure_wave_channel_checks {
    if (-not $script:wave_form -or $script:wave_form.IsDisposed) {
        return
    }

    while ($script:wave_channel_checks.Count -lt $script:wave_ch_values.Count) {
        $index = $script:wave_channel_checks.Count
        $checkbox = New-Object DarkCheckBox
        $checkbox.Text = get_wave_channel_display_name -index $index
        $checkbox.AutoSize = $true
        $checkbox.Checked = $true
        $checkbox.ForeColor = (get_wave_channel_color -index $index)
        # 通道勾选框同样开自绘（否则系统画白方框）；BackColor 给不透明面板色，
        # 避免 Transparent 下原生文字与自绘文字叠成重影。
        $checkbox.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $checkbox.UseDark = $true
        $checkbox.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
        $checkbox.BoxBorder = [System.Drawing.Color]::FromArgb(150, 158, 172)
        $checkbox.MarkColor = (get_wave_channel_color -index $index)
        $checkbox.Add_CheckedChanged({
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        })

        # 色块：通道颜色方块，点击=显示/隐藏该通道（比勾选框更好点，色盲友好靠 ✎/文字兜底）
        $swatch = New-Object System.Windows.Forms.Label
        $swatch.Size = New-Object System.Drawing.Size(14, 14)
        $swatch.BackColor = (get_wave_channel_color -index $index)
        $swatch.Location = New-Object System.Drawing.Point(0, 5)
        $swatch.Cursor = [System.Windows.Forms.Cursors]::Hand
        $swatch.Tag = $index
        $swatch.Add_Click({
            param($sender, $event_args)
            $idx = [int]$sender.Tag
            if ($idx -ge 0 -and $idx -lt $script:wave_channel_checks.Count) {
                $script:wave_channel_checks[$idx].Checked = -not $script:wave_channel_checks[$idx].Checked
            }
        })

        # CheckBox 不触发双击事件（点击全被勾选切换吃掉），用 ✎ 小按钮触发重命名
        $rename_button = New-Object System.Windows.Forms.Button
        $rename_button.Text = '✎'
        $rename_button.Tag = $index
        $rename_button.Size = New-Object System.Drawing.Size(22, 20)
        $rename_button.Location = New-Object System.Drawing.Point(120, 2)
        $rename_button.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
        $rename_button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $rename_button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(70, 82, 105)
        $rename_button.BackColor = [System.Drawing.Color]::FromArgb(42, 50, 68)
        $rename_button.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)
        $rename_button.Add_Click({
            param($sender, $event_args)
            show_channel_rename_dialog -index ([int]$sender.Tag)
        })

        # 行面板固定高度、宽度由 sync_wave_panel_row_widths 统一，✎ 右锚定 ——
        # 排版不随通道名文字长度变化（名字过长时勾选框文本截断）
        $row_panel = New-Object System.Windows.Forms.Panel
        $row_panel.Width = (get_wave_xy_section_inner_width)
        $row_panel.Height = 24
        $row_panel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 2)
        $row_panel.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
        $checkbox.Location = New-Object System.Drawing.Point(18, 2)
        $checkbox.MaximumSize = New-Object System.Drawing.Size(($row_panel.Width - 50), 0)
        $rename_button.Location = New-Object System.Drawing.Point(($row_panel.Width - 24), 2)
        $row_panel.Controls.Add($swatch)
        $row_panel.Controls.Add($checkbox)
        $row_panel.Controls.Add($rename_button)

        [void]$script:wave_channel_checks.Add($checkbox)
        [void]$script:wave_channel_flow.Controls.Add($row_panel)
    }

    # 已有 checkbox 的名字与 names 列表保持同步（加载记忆/新通道）
    for ($i = 0; $i -lt $script:wave_channel_checks.Count; $i++) {
        $default_text = 'CH{0}' -f $i
        $saved_name = ''
        if ($i -lt $script:wave_channel_names.Count) {
            $saved_name = [string]$script:wave_channel_names[$i]
        }
        if ([string]::IsNullOrWhiteSpace($saved_name)) {
            $script:wave_channel_checks[$i].Text = $default_text
        }
        else {
            $script:wave_channel_checks[$i].Text = $saved_name
        }
    }

    # XY 相位图控制区跟随通道行一起重建/回挂（清空缓冲后面板被清，靠这里恢复）
    ensure_wave_xy_section
}

function style_wave_button {
    # 扁平深色按钮；-accent = 主操作（实底强调色）。
    # 状态感由 FlatAppearance 提供：悬停变亮 / 按下变暗，无需事件处理器
    param([System.Windows.Forms.Button]$button, [switch]$accent)

    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderSize = 1
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    if ($accent) {
        $button.BackColor = $script:theme.Accent
        $button.FlatAppearance.BorderColor = $script:theme.Accent
        $button.ForeColor = [System.Drawing.Color]::White
        $button.FlatAppearance.MouseOverBackColor = $script:theme.AccentHover
        $button.FlatAppearance.MouseDownBackColor = $script:theme.AccentDown
    }
    else {
        $button.BackColor = $script:theme.BtnFace
        $button.FlatAppearance.BorderColor = $script:theme.Border
        $button.ForeColor = $script:theme.TextMain
        $button.FlatAppearance.MouseOverBackColor = $script:theme.BtnHover
        $button.FlatAppearance.MouseDownBackColor = $script:theme.BtnDown
    }
}

function set_wave_button_active {
    param(
        [System.Windows.Forms.Button]$button,
        [bool]$active
    )

    if ($active) {
        $button.BackColor = [System.Drawing.Color]::FromArgb(32, 92, 66)
        $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(70, 160, 110)
    }
    else {
        $button.BackColor = [System.Drawing.Color]::FromArgb(42, 50, 68)
        $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(70, 82, 105)
    }
}

function get_wave_slider_metrics {
    # 滑块几何：返回轨道范围（与绘制、命中检测共用，保证三者一致）
    param([int]$width)

    $margin = 14
    return [pscustomobject]@{
        Left = $margin
        Right = ($width - $margin)
        Span = [Math]::Max(1, $width - 2 * $margin)
        MidY = $width * 0.0   # 占位（y 由调用方按高度算）
    }
}

function get_wave_slider_frame_x {
    # 帧号 → 滑块轨道像素 x
    param([double]$frame, [int]$total, [int]$track_left, [int]$track_span)

    $denom = [Math]::Max(1, $total - 1)
    return $track_left + ($frame / $denom) * $track_span
}

function draw_wave_slider {
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height
    )

    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $track_y = [float]($height / 2.0)
    $metrics = get_wave_slider_metrics -width $width
    $track_left = $metrics.Left
    $track_right = $metrics.Right

    # 背景轨道
    $track_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(70, 80, 98))
    $track_pen.Width = 2
    $graphics.DrawLine($track_pen, [float]$track_left, $track_y, [float]$track_right, $track_y)
    $track_pen.Dispose()

    $total = $script:wave_frame_count
    if ($total -lt 2) {
        return
    }

    $view = get_wave_view_range -total $total
    $x_start = get_wave_slider_frame_x -frame $view.Start -total $total -track_left $track_left -track_span $metrics.Span
    $x_end = get_wave_slider_frame_x -frame ($view.End - 1) -total $total -track_left $track_left -track_span $metrics.Span
    $x_mid = ($x_start + $x_end) / 2.0

    # 选中范围高亮条
    $range_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(64, 190, 170))
    $range_pen.Width = 5
    $graphics.DrawLine($range_pen, [float]$x_start, $track_y, [float]$x_end, $track_y)
    $range_pen.Dispose()

    # 三个圆点：红 = 窗口起点，绿 = 中心（自动保持在两点正中），紫 = 窗口终点
    $dot_radius = 6.0
    $hover_point = $script:wave_slider_hover
    if ($script:wave_slider_drag) {
        $hover_point = $script:wave_slider_drag.Point
    }
    $dot_colors = @(
        @{ X = $x_start; Color = [System.Drawing.Color]::FromArgb(232, 74, 74); Key = 'start' },
        @{ X = $x_mid; Color = [System.Drawing.Color]::FromArgb(72, 201, 132); Key = 'mid' },
        @{ X = $x_end; Color = [System.Drawing.Color]::FromArgb(150, 61, 177); Key = 'end' }
    )
    foreach ($dot in $dot_colors) {
        # 悬停/拖动中的点先画高亮外圈
        if ($dot.Key -eq $hover_point) {
            $halo_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(235, 240, 250))
            $halo_pen.Width = 2
            $graphics.DrawEllipse($halo_pen, [float]($dot.X - 9.5), [float]($track_y - 9.5), 19.0, 19.0)
            $halo_pen.Dispose()
        }
        $brush = New-Object System.Drawing.SolidBrush($dot.Color)
        $graphics.FillEllipse($brush, [float]($dot.X - $dot_radius), [float]($track_y - $dot_radius), [float]($dot_radius * 2), [float]($dot_radius * 2))
        $brush.Dispose()
    }

    if (-not $view.Follow) {
        $hint_font = new_font -size 8.25
        $hint_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 170, 90))
        $graphics.DrawString('历史浏览中 — 拖到最右端或点「自动范围」回实时', $hint_font, $hint_brush, [float]($track_left + 4), 1.0)
        $hint_brush.Dispose()
        $hint_font.Dispose()
    }
}

function load_wave_plugins {
    # 合同 v2：注册即页签。第 0 页固定为「波形」，插件页按注册顺序追加；
    # 页签内容懒加载——首次切入才调 OpenPanel（传入页签容器），异常只移除该页签
    if (-not $script:wave_form -or $script:wave_form.IsDisposed) {
        return
    }
    if (-not $script:wave_tabs -or $script:wave_tabs.IsDisposed) {
        return
    }

    # 重建时只清插件页（≥1），保留索引 0 的「波形」页
    for ($i = $script:wave_tabs.Controls.Count - 1; $i -ge 1; $i--) {
        $script:wave_tabs.Controls.RemoveAt($i)
    }

    foreach ($plugin in $script:wave_plugins) {
        $page = New-Object System.Windows.Forms.TabPage
        $page.Text = $plugin.Name
        $page.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
        $page.Tag = @{ Panel = $plugin.OpenPanel; Built = $false }
        [void]$script:wave_tabs.Controls.Add($page)
    }
}

function get_wave_plot_rect {
    $width = $script:wave_panel.ClientSize.Width
    $height = $script:wave_panel.ClientSize.Height
    [pscustomobject]@{
        Left = $script:wave_plot_left
        Top = $script:wave_plot_top
        Width = $width - $script:wave_plot_left - $script:wave_plot_right
        Height = $height - $script:wave_plot_top - $script:wave_plot_bottom
    }
}

# ---------- XY 相位图（IQ 李萨如/星座图） ----------
# X 轴 = 通道X，Y 轴 = 通道Y，逐帧打点成相位轨迹；余晖 = 累积位图按时间常数衰减
# （乒乓双位图 + ColorMatrix alpha 缩放），复刻模拟示波器荧光余辉。

function get_wave_xy_persist_seconds {
    # 余晖档位 → 秒。-1 = 常驻（不衰减），0 = 关（每帧清屏重画）
    switch ([string]$script:wave_xy_persist) {
        '0.5s' { return 0.5 }
        '1s' { return 1.0 }
        '2s' { return 2.0 }
        '5s' { return 5.0 }
        '常驻' { return -1.0 }
        default { return 0.0 }
    }
}

function get_wave_xy_count {
    # XY 窗口个数：只允许 1 / 2 / 4 三档（默认 2），其余值回落 2
    $count = [int]$script:wave_xy_count
    if ($count -ne 1 -and $count -ne 2 -and $count -ne 4) { $count = 2 }
    return $count
}

function get_wave_xy_pair {
    # 第 slot 个窗口的 X/Y 源通道（夹进现有通道范围；默认配对 CH0/1、CH2/3、CH4/5、CH6/7）
    param([int]$slot)

    $count = $script:wave_ch_values.Count
    if ($count -lt 1) {
        return [pscustomobject]@{ X = 0; Y = 0 }
    }
    $x = [int]$script:wave_xy_pairs[$slot * 2]
    $y = [int]$script:wave_xy_pairs[$slot * 2 + 1]
    if ($x -lt 0 -or $x -ge $count) { $x = $count - 1 }
    if ($y -lt 0 -or $y -ge $count) { $y = $count - 1 }
    return [pscustomobject]@{ X = $x; Y = $y }
}

function get_wave_xy_grid {
    # 窗口栅格（格子必须正方形 —— IQ 正交性靠圆看起来是圆来判断）：
    # 1 = 独占；2 = 左右双屏；4 = 2×2。整块栅格在绘图区内居中（放不下就留边）。
    param([int]$plot_width, [int]$plot_height)

    $count = get_wave_xy_count
    $cols = 1
    $rows = 1
    if ($count -eq 2) {
        $cols = 2
    }
    elseif ($count -eq 4) {
        $cols = 2
        $rows = 2
    }
    $gap = 6
    $avail_w = ($plot_width - $gap * ($cols - 1)) / $cols
    $avail_h = ($plot_height - $gap * ($rows - 1)) / $rows
    $side = [int][Math]::Max(40, [Math]::Min($avail_w, $avail_h))
    $block_w = $side * $cols + $gap * ($cols - 1)
    $block_h = $side * $rows + $gap * ($rows - 1)
    return [pscustomobject]@{
        Count = $count; Cols = $cols; Rows = $rows; Gap = $gap
        CellW = $side; CellH = $side
        OffX = [int](($plot_width - $block_w) / 2)
        OffY = [int](($plot_height - $block_h) / 2)
    }
}

function get_wave_xy_cell_rect {
    # 第 index 个窗口在绘图区内的局部矩形（原点 = 绘图区左上，含栅格居中偏移）
    param([int]$index, [int]$plot_width, [int]$plot_height)

    $grid = get_wave_xy_grid -plot_width $plot_width -plot_height $plot_height
    $col = $index % $grid.Cols
    $row = [int][Math]::Floor($index / $grid.Cols)
    return [pscustomobject]@{
        X = ($grid.OffX + $col * ($grid.CellW + $grid.Gap))
        Y = ($grid.OffY + $row * ($grid.CellH + $grid.Gap))
        W = $grid.CellW
        H = $grid.CellH
    }
}

function get_wave_xy_span_half {
    # 量程档位 → 半幅 N（两轴 [-N,+N]）；0 = 自适应（自动拟合）。按窗口独立
    param([int]$slot)

    $mode = '±8'
    if ($slot -ge 0 -and $slot -lt 4) { $mode = [string]$script:wave_xy_spans[$slot] }
    switch ($mode) {
        '自适应' { return 0 }
        '±2' { return 2 }
        '±4' { return 4 }
        '±8' { return 8 }
        '±16' { return 16 }
        '±32' { return 32 }
        default { return 8 }
    }
}

function get_wave_xy_divisions {
    # 每轴分格数（默认 8 → ±8 时每格 2 个单位）。按窗口独立
    param([int]$slot)

    $divs = 8
    if ($slot -ge 0 -and $slot -lt 4) { $divs = [int]$script:wave_xy_divs_arr[$slot] }
    if ($divs -lt 2) { $divs = 8 }
    if ($divs -gt 16) { $divs = 16 }
    return $divs
}

function get_wave_xy_mapping {
    # XY 映射（单窗口，量程/分格按窗口独立）。默认固定量程：两轴 [-N,+N]、每轴等分
    # divs 格（IQ 相位图标准用法，中心 0、刻度落在整格上；映射恒定 → 余晖绝不因
    # 量程抖动清屏）。'自适应' 档才走自动拟合：最近「窗口」帧数值范围 → 等比例 → k 量化防抖。
    param([int]$x_ch, [int]$y_ch, [int]$plot_width, [int]$plot_height, [int]$slot = 0)

    $span_half = get_wave_xy_span_half -slot $slot
    if ($span_half -gt 0) {
        $span = 2.0 * $span_half
        $divs = get_wave_xy_divisions -slot $slot
        $step = $span / $divs
        return [pscustomobject]@{
            K = ($span / [Math]::Max(1, $plot_width))
            XMin = -$span_half; XSpan = $span; XStep = $step
            YMin = -$span_half; YSpan = $span; YStep = $step
        }
    }

    $total = $script:wave_frame_count
    $window = [int]$script:wave_window_frames
    if ($window -lt 50) { $window = 50 }

    $x_min = [double]::PositiveInfinity
    $x_max = [double]::NegativeInfinity
    $y_min = [double]::PositiveInfinity
    $y_max = [double]::NegativeInfinity
    if ($total -gt 0 -and $x_ch -lt $script:wave_ch_values.Count -and $y_ch -lt $script:wave_ch_values.Count) {
        $visible = [Math]::Min($total, $window)
        $start = $total - $visible
        $x_data = get_wave_view_data -channel $x_ch -start $start -end $total
        $y_data = get_wave_view_data -channel $y_ch -start $start -end $total
        $x_arr = $x_data.Data
        $y_arr = $y_data.Data
        $n = [Math]::Min($x_data.Length, $y_data.Length)
        for ($j = 0; $j -lt $n; $j++) {
            $vx = $x_arr[$x_data.Offset + $j]
            $vy = $y_arr[$y_data.Offset + $j]
            if ([double]::IsNaN($vx) -or [double]::IsInfinity($vx) -or [double]::IsNaN($vy) -or [double]::IsInfinity($vy)) {
                continue
            }
            if ($vx -lt $x_min) { $x_min = $vx }
            if ($vx -gt $x_max) { $x_max = $vx }
            if ($vy -lt $y_min) { $y_min = $vy }
            if ($vy -gt $y_max) { $y_max = $vy }
        }
    }

    if ($x_min -gt $x_max) { $x_min = -1.0; $x_max = 1.0 }
    if ($y_min -gt $y_max) { $y_min = -1.0; $y_max = 1.0 }

    # 等比例：k 取两轴需求的较大者 → 非正方形绘图区里圆也是圆
    $safe_w = [Math]::Max(1, $plot_width)
    $safe_h = [Math]::Max(1, $plot_height)
    $k = [Math]::Max((($x_max - $x_min) / $safe_w), (($y_max - $y_min) / $safe_h))
    if ([double]::IsNaN($k) -or [double]::IsInfinity($k) -or $k -le 0) {
        $k = 2.0 / $safe_h
    }

    # 12% 视觉边距 + k 量化（2 的 48 分之 n 次幂台阶 ≈ 1.45% 步进）
    $k = $k * 1.12
    $k = [Math]::Pow(2.0, [Math]::Ceiling([Math]::Log($k, 2.0) * 48.0) / 48.0)

    $span_x = $k * $safe_w
    $span_y = $k * $safe_h
    $step_x = get_nice_step -range $span_x -target_lines 6
    $step_y = get_nice_step -range $span_y -target_lines 6
    $cx = [Math]::Round((($x_min + $x_max) / 2.0) / $step_x) * $step_x
    $cy = [Math]::Round((($y_min + $y_max) / 2.0) / $step_y) * $step_y

    return [pscustomobject]@{
        K = $k
        XMin = ($cx - $span_x / 2.0); XSpan = $span_x; XStep = $step_x
        YMin = ($cy - $span_y / 2.0); YSpan = $span_y; YStep = $step_y
    }
}

function get_wave_xy_map_stamp {
    param($map)
    return ('{0:E10}|{1:E10}|{2:E10}' -f $map.K, $map.XMin, $map.YMin)
}

function dispose_wave_xy_bitmaps {
    for ($i = 0; $i -lt 4; $i++) {
        foreach ($bmp in @($script:wave_xy_accum_a[$i], $script:wave_xy_accum_b[$i])) {
            if ($bmp) {
                try { $bmp.Dispose() } catch { }
            }
        }
        $script:wave_xy_accum_a[$i] = $null
        $script:wave_xy_accum_b[$i] = $null
        $script:wave_xy_accum_wh[$i] = $null
        $script:wave_xy_map_stamp[$i] = ''
        $script:wave_xy_map_prev[$i] = $null
    }
}

function clear_wave_xy_pixels {
    for ($i = 0; $i -lt 4; $i++) {
        if ($null -eq $script:wave_xy_accum_a[$i]) { continue }
        $g = [System.Drawing.Graphics]::FromImage($script:wave_xy_accum_a[$i])
        try {
            $g.Clear([System.Drawing.Color]::Transparent)
        }
        finally {
            $g.Dispose()
        }
    }
}

function fade_wave_xy_accum {
    # 乒乓衰减（单槽位）：A 按 alpha×f − 0.75 写进 B 再交换。−0.75 项让低亮度像素
    # 突破 GDI+ 取整量化最终归零，不留永驻浅痕；1:1 拷贝用整型矩形避免重采样抖动。
    param([int]$slot, [double]$factor)

    $src = $script:wave_xy_accum_a[$slot]
    $dst = $script:wave_xy_accum_b[$slot]
    if ($null -eq $src -or $null -eq $dst) { return }
    if ($factor -ge 0.9999) { return }

    $g = [System.Drawing.Graphics]::FromImage($dst)
    try {
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
        $matrix = New-Object System.Drawing.Imaging.ColorMatrix
        $matrix.Matrix33 = [float]$factor
        $matrix.Matrix43 = [float](-0.75 / 255.0)
        $attr = New-Object System.Drawing.Imaging.ImageAttributes
        $attr.SetColorMatrix($matrix)
        try {
            $rect = New-Object System.Drawing.Rectangle(0, 0, $src.Width, $src.Height)
            $g.DrawImage($src, $rect, 0, 0, $src.Width, $src.Height, [System.Drawing.GraphicsUnit]::Pixel, $attr)
        }
        finally {
            $attr.Dispose()
        }
    }
    finally {
        $g.Dispose()
    }
    $script:wave_xy_accum_a[$slot] = $dst
    $script:wave_xy_accum_b[$slot] = $src
}

function migrate_wave_xy_accum {
    # 映射台阶/中心变化时把旧余晖拉伸平移到新映射（等比例缩放 s = k_old/k_new），
    # 缩放超界（数据突变 >8×）直接清屏重来。
    param([int]$slot, $prev, $map, [int]$plot_width, [int]$plot_height)

    $src = $script:wave_xy_accum_a[$slot]
    $dst = $script:wave_xy_accum_b[$slot]
    if ($null -eq $src -or $null -eq $dst -or $null -eq $prev) { return }
    $ratio = $prev.K / $map.K
    if ([double]::IsNaN($ratio) -or [double]::IsInfinity($ratio) -or $ratio -lt 0.12 -or $ratio -gt 8.0) {
        $g = [System.Drawing.Graphics]::FromImage($src)
        try { $g.Clear([System.Drawing.Color]::Transparent) } finally { $g.Dispose() }
        return
    }
    # 旧迹右下角（XMax_old, YMax_old）在新坐标系下的像素位置决定平移量
    $dx = (($prev.XMin + $prev.XSpan) - ($map.XMin + $map.XSpan)) / $map.K + ($plot_width - $plot_width * $ratio)
    $dy = (($map.YMin + $map.YSpan) - ($prev.YMin + $prev.YSpan)) / $map.K

    $g = [System.Drawing.Graphics]::FromImage($dst)
    try {
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::Bilinear
        $dest = New-Object System.Drawing.RectangleF([float]$dx, [float]$dy, [float]($plot_width * $ratio), [float]($plot_height * $ratio))
        $src_rect = New-Object System.Drawing.RectangleF(0.0, 0.0, [float]$src.Width, [float]$src.Height)
        $g.DrawImage($src, $dest, $src_rect, [System.Drawing.GraphicsUnit]::Pixel)
    }
    finally {
        $g.Dispose()
    }
    $script:wave_xy_accum_a[$slot] = $dst
    $script:wave_xy_accum_b[$slot] = $src
}

function draw_wave_xy_range {
    # 把缓冲区间 [start_idx, end_idx) 的 (X,Y) 点对画进目标 Graphics
    # （累积层坐标系：原点 = 窗口单元左上；面板直绘时调用方先 TranslateTransform）。
    # 连续有效点连成轨迹线，NaN/出界断线，孤立点画 2px 方点。
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$x_ch,
        [int]$y_ch,
        [int]$start_idx,
        [int]$end_idx,
        $map,
        [int]$plot_width,
        [int]$plot_height,
        [int]$alpha = 255
    )

    if ($end_idx -le $start_idx -or $plot_width -lt 4 -or $plot_height -lt 4) { return }
    $count = $script:wave_frame_count
    if ($count -lt 1) { return }
    if ($start_idx -lt 0) { $start_idx = 0 }
    if ($start_idx -gt ($count - 1)) { return }
    if ($end_idx -gt $count) { $end_idx = $count }

    if ($x_ch -ge $script:wave_ch_values.Count -or $y_ch -ge $script:wave_ch_values.Count -or $x_ch -lt 0 -or $y_ch -lt 0) { return }

    $x_data = get_wave_view_data -channel $x_ch -start $start_idx -end $end_idx
    $y_data = get_wave_view_data -channel $y_ch -start $start_idx -end $end_idx
    $x_arr = $x_data.Data
    $y_arr = $y_data.Data
    $n = [Math]::Min($x_data.Length, $y_data.Length)
    if ($n -lt 1) { return }

    # 点数远超像素量级时抽稀，控住 GDI+ 耗时
    $stride = 1
    if ($n -gt 8000) { $stride = [int][Math]::Ceiling($n / 8000.0) }

    $trace_color = get_wave_channel_color -index $y_ch
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb($alpha, $trace_color.R, $trace_color.G, $trace_color.B))
    $pen.Width = 1.5
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $dot_brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alpha, $trace_color.R, $trace_color.G, $trace_color.B))

    $run = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
    $margin = 2.0
    for ($j = 0; $j -lt $n; $j += $stride) {
        $vx = $x_arr[$x_data.Offset + $j]
        $vy = $y_arr[$y_data.Offset + $j]
        $broken = $false
        if ([double]::IsNaN($vx) -or [double]::IsInfinity($vx) -or [double]::IsNaN($vy) -or [double]::IsInfinity($vy)) {
            $broken = $true
        }
        else {
            $px = (($vx - $map.XMin) / $map.XSpan) * $plot_width
            $py = $plot_height - (($vy - $map.YMin) / $map.YSpan) * $plot_height
            if ($px -lt -$margin -or $px -gt ($plot_width + $margin) -or $py -lt -$margin -or $py -gt ($plot_height + $margin)) {
                # 出界断线，避免横跨整屏的假连线
                $broken = $true
            }
            else {
                [void]$run.Add([System.Drawing.PointF]::new([float]$px, [float]$py))
            }
        }
        if ($broken -or ($j + $stride -ge $n)) {
            if ($run.Count -gt 1) {
                $graphics.DrawLines($pen, $run.ToArray())
            }
            elseif ($run.Count -eq 1) {
                $graphics.FillRectangle($dot_brush, $run[0].X - 1.0, $run[0].Y - 1.0, 2.0, 2.0)
            }
            $run.Clear()
        }
    }
    $dot_brush.Dispose()
    $pen.Dispose()
}

function rebuild_wave_xy_accum {
    # 重建全部窗口槽位的累积层：清旧位图 → 按当前窗口单元尺寸建新 →
    # 最近「窗口」帧一次铺入（进 XY/换源/换余晖/改窗口数后立即有画面）。
    param([int]$plot_width = 0, [int]$plot_height = 0)

    dispose_wave_xy_bitmaps
    $script:wave_xy_last_abs = [long]$script:wave_frame_total
    $script:wave_xy_last_tick = [datetime]::Now

    if (-not $script:wave_xy_on) { return }
    if ($plot_width -le 0 -or $plot_height -le 0) {
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            $plot = get_wave_plot_rect
            $plot_width = $plot.Width
            $plot_height = $plot.Height
        }
    }
    if ($plot_width -lt 40 -or $plot_height -lt 40) { return }

    $grid = get_wave_xy_grid -plot_width $plot_width -plot_height $plot_height
    $total = $script:wave_frame_count
    $window = [int]$script:wave_window_frames
    if ($window -lt 50) { $window = 50 }
    $visible = [Math]::Min($total, $window)

    for ($i = 0; $i -lt $grid.Count; $i++) {
        try {
            $script:wave_xy_accum_a[$i] = New-Object System.Drawing.Bitmap($grid.CellW, $grid.CellH)
            $script:wave_xy_accum_b[$i] = New-Object System.Drawing.Bitmap($grid.CellW, $grid.CellH)
        }
        catch {
            dispose_wave_xy_bitmaps
            return
        }
        $script:wave_xy_accum_wh[$i] = @{ W = $grid.CellW; H = $grid.CellH }

        $pair = get_wave_xy_pair -slot $i
        $map = get_wave_xy_mapping -x_ch $pair.X -y_ch $pair.Y -plot_width $grid.CellW -plot_height $grid.CellH -slot $i
        $script:wave_xy_map_prev[$i] = $map
        $script:wave_xy_map_stamp[$i] = get_wave_xy_map_stamp -map $map

        if ($total -ge 1) {
            $g = [System.Drawing.Graphics]::FromImage($script:wave_xy_accum_a[$i])
            try {
                draw_wave_xy_range -graphics $g -x_ch $pair.X -y_ch $pair.Y -start_idx ($total - $visible) -end_idx $total -map $map -plot_width $grid.CellW -plot_height $grid.CellH
            }
            finally {
                $g.Dispose()
            }
        }
    }
}

function update_wave_xy_accum {
    # XY 余晖推进（全部窗口）：映射变化 → 拉伸迁移旧迹；按时间常数衰减；
    # 追加自上次的新帧。未开窗/暂停/XY 未开直接返回；plot_width/height 供自测离屏调用。
    param([datetime]$now, [int]$plot_width = 0, [int]$plot_height = 0)

    if (-not $script:wave_xy_on) { return }
    if ($plot_width -le 0 -or $plot_height -le 0) {
        if (-not $script:wave_panel -or $script:wave_panel.IsDisposed) { return }
        $plot = get_wave_plot_rect
        $plot_width = $plot.Width
        $plot_height = $plot.Height
    }
    if ($plot_width -lt 40 -or $plot_height -lt 40) { return }
    if ($script:wave_paused) { return }

    $grid = get_wave_xy_grid -plot_width $plot_width -plot_height $plot_height

    # 任一槽位缺失/尺寸不符（首次/改窗口数/resize）→ 全部重建
    for ($i = 0; $i -lt $grid.Count; $i++) {
        $wh = $script:wave_xy_accum_wh[$i]
        if ($null -eq $script:wave_xy_accum_a[$i] -or $null -eq $wh -or $wh.W -ne $grid.CellW -or $wh.H -ne $grid.CellH) {
            rebuild_wave_xy_accum -plot_width $plot_width -plot_height $plot_height
            break
        }
    }
    if ($null -eq $script:wave_xy_accum_a[0]) { return }

    # 余晖衰减：时间常数 = 档位秒数；关 = 清屏；常驻 = 不衰减（衰减系数各窗口相同）
    $persist = get_wave_xy_persist_seconds
    $dt = ($now - $script:wave_xy_last_tick).TotalSeconds
    if ($dt -le 0 -or $dt -gt 0.5) { $dt = 0.066 }

    # 追加新帧（绝对帧号 → 缓冲下标；缓冲截断后从可见段起；各窗口共用同一帧区间）
    $abs_total = [long]$script:wave_frame_total
    $buf_start_abs = $abs_total - [long]$script:wave_frame_count
    $from_abs = [long]$script:wave_xy_last_abs
    if ($from_abs -lt $buf_start_abs) { $from_abs = $buf_start_abs }
    $has_new = ($from_abs -lt $abs_total)
    $start_idx = [int]($from_abs - $buf_start_abs)
    $end_idx = [int]($abs_total - $buf_start_abs)

    for ($i = 0; $i -lt $grid.Count; $i++) {
        $pair = get_wave_xy_pair -slot $i
        $map = get_wave_xy_mapping -x_ch $pair.X -y_ch $pair.Y -plot_width $grid.CellW -plot_height $grid.CellH -slot $i
        $stamp = get_wave_xy_map_stamp -map $map
        if ($script:wave_xy_map_stamp[$i] -ne '' -and $stamp -ne $script:wave_xy_map_stamp[$i]) {
            migrate_wave_xy_accum -slot $i -prev $script:wave_xy_map_prev[$i] -map $map -plot_width $grid.CellW -plot_height $grid.CellH
        }
        $script:wave_xy_map_prev[$i] = $map
        $script:wave_xy_map_stamp[$i] = $stamp

        if ($persist -eq 0.0) {
            $g = [System.Drawing.Graphics]::FromImage($script:wave_xy_accum_a[$i])
            try { $g.Clear([System.Drawing.Color]::Transparent) } finally { $g.Dispose() }
        }
        elseif ($persist -gt 0) {
            fade_wave_xy_accum -slot $i -factor ([Math]::Exp(-$dt / $persist))
        }

        if ($has_new) {
            $g = [System.Drawing.Graphics]::FromImage($script:wave_xy_accum_a[$i])
            try {
                draw_wave_xy_range -graphics $g -x_ch $pair.X -y_ch $pair.Y -start_idx $start_idx -end_idx $end_idx -map $map -plot_width $grid.CellW -plot_height $grid.CellH
            }
            finally {
                $g.Dispose()
            }
        }
    }
    if ($has_new) {
        $script:wave_xy_last_abs = $abs_total
    }
    $script:wave_xy_last_tick = $now
}

function draw_wave_xy_graphics {
    # XY 相位图渲染（多窗口栅格）：等比例网格 + 各窗口余晖累积层叠加；
    # -direct 跳过累积层全量直绘（导出 PNG / 累积层未就绪时的回退路径）。
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height,
        [switch]$direct
    )

    $background_brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.Point(0, 0)),
        (New-Object System.Drawing.Point(0, $height)),
        $script:theme.PlotBgTop,
        $script:theme.PlotBgBottom)
    $graphics.FillRectangle($background_brush, 0, 0, $width, $height)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $plot_left = $script:wave_plot_left
    $plot_top = $script:wave_plot_top
    $plot_width = $width - $script:wave_plot_left - $script:wave_plot_right
    $plot_height = $height - $script:wave_plot_top - $script:wave_plot_bottom
    if ($plot_width -lt 40 -or $plot_height -lt 40) {
        $background_brush.Dispose()
        return
    }

    $grid = get_wave_xy_grid -plot_width $plot_width -plot_height $plot_height
    $total = $script:wave_frame_count

    $frame_pen = New-Object System.Drawing.Pen($script:theme.PlotFrame)
    $grid_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 55, 70))
    $zero_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(90, 105, 125))
    $label_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(168, 178, 195))
    $text_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(215, 222, 235))
    $axis_font = new_font -size 8.25
    $legend_font = new_font -size 8.5
    $cell_marks = @([char]0x2460, [char]0x2461, [char]0x2462, [char]0x2463)   # ①②③④

    for ($i = 0; $i -lt $grid.Count; $i++) {
        $cell = get_wave_xy_cell_rect -index $i -plot_width $plot_width -plot_height $plot_height
        $cx = $plot_left + $cell.X
        $cy = $plot_top + $cell.Y
        $pair = get_wave_xy_pair -slot $i
        $map = get_wave_xy_mapping -x_ch $pair.X -y_ch $pair.Y -plot_width $cell.W -plot_height $cell.H -slot $i

        $graphics.DrawRectangle($frame_pen, [float]$cx, [float]$cy, [float]$cell.W, [float]$cell.H)

        # 垂直网格（X 值刻度）：标签画在格线旁、窗内左下角区域（多窗口不串位）
        $tick = [Math]::Ceiling($map.XMin / $map.XStep) * $map.XStep
        $guard = 0
        while (($tick -le ($map.XMin + $map.XSpan)) -and ($guard -lt 32)) {
            $xx = $cx + (($tick - $map.XMin) / $map.XSpan) * $cell.W
            if ([Math]::Abs($tick) -lt ($map.XStep * 0.001)) {
                $graphics.DrawLine($zero_pen, [float]$xx, [float]$cy, [float]$xx, ($cy + $cell.H))
            }
            else {
                $graphics.DrawLine($grid_pen, [float]$xx, [float]$cy, [float]$xx, ($cy + $cell.H))
            }
            $label_x = [Math]::Max(($cx + 2), ($xx - 8))
            $graphics.DrawString($tick.ToString('G4'), $axis_font, $label_brush, [float]$label_x, [float]($cy + $cell.H - 15))
            $tick += $map.XStep
            $guard++
        }

        # 水平网格（Y 值刻度）：标签贴格线、窗内左缘
        $tick = [Math]::Ceiling($map.YMin / $map.YStep) * $map.YStep
        $guard = 0
        while (($tick -le ($map.YMin + $map.YSpan)) -and ($guard -lt 32)) {
            $yy = $cy + $cell.H - (($tick - $map.YMin) / $map.YSpan) * $cell.H
            if ([Math]::Abs($tick) -lt ($map.YStep * 0.001)) {
                $graphics.DrawLine($zero_pen, [float]$cx, [float]$yy, ($cx + $cell.W), [float]$yy)
            }
            else {
                $graphics.DrawLine($grid_pen, [float]$cx, [float]$yy, ($cx + $cell.W), [float]$yy)
            }
            $label_y = [Math]::Max(($cy + 2), ($yy - 7))
            $graphics.DrawString($tick.ToString('G4'), $axis_font, $label_brush, [float]($cx + 3), [float]$label_y)
            $tick += $map.YStep
            $guard++
        }

        # 轨迹：优先叠加该窗口的余晖累积层；-direct（导出）或累积层未就绪时全量直绘
        $accum = $script:wave_xy_accum_a[$i]
        if ($total -gt 0 -and ($direct -or $null -eq $accum)) {
            $window = [int]$script:wave_window_frames
            if ($window -lt 50) { $window = 50 }
            $visible = [Math]::Min($total, $window)
            $graphics.TranslateTransform($cx, $cy)
            try {
                draw_wave_xy_range -graphics $graphics -x_ch $pair.X -y_ch $pair.Y -start_idx ($total - $visible) -end_idx $total -map $map -plot_width $cell.W -plot_height $cell.H
            }
            finally {
                $graphics.ResetTransform()
            }
        }
        elseif ($null -ne $accum) {
            $graphics.DrawImage($accum, $cx, $cy, $cell.W, $cell.H)
        }

        # 每窗口图例：序号 + X/Y 源通道色标
        $legend_x = $cx + 6
        $legend_y = $cy + 4
        $seg1 = ('{0} X: {1}' -f $cell_marks[$i], (get_wave_channel_display_name -index $pair.X))
        $size1 = $graphics.MeasureString($seg1, $legend_font)
        $graphics.DrawString($seg1, $legend_font, $text_brush, [float]($legend_x + 12), [float]$legend_y)
        $seg2_x = $legend_x + 12 + $size1.Width + 4
        $swatch_y = New-Object System.Drawing.SolidBrush((get_wave_channel_color -index $pair.Y))
        $graphics.FillRectangle($swatch_y, [float]$seg2_x, [float]($legend_y + 3), 8, 8)
        $swatch_y.Dispose()
        $graphics.DrawString(('Y: {0}' -f (get_wave_channel_display_name -index $pair.Y)), $legend_font, $text_brush, [float]($seg2_x + 12), [float]$legend_y)
    }

    $legend_font.Dispose()

    if ($total -lt 1) {
        $hint_font = new_font -size 10
        $hint_text = 'XY 相位图：等待数据（IQ 信号：I 接 X，Q 接 Y；右侧可选窗口个数与源通道）'
        $size_text = $graphics.MeasureString($hint_text, $hint_font)
        $graphics.DrawString($hint_text, $hint_font, $label_brush, [float](($width - $size_text.Width) / 2), [float](($height - $size_text.Height) / 2))
        $hint_font.Dispose()
    }

    if ($script:wave_paused) {
        $paused_font = new_font -size 9 -style Bold
        $paused_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 120, 110))
        $graphics.DrawString('已暂停', $paused_font, $paused_brush, ($width - 72), ($plot_top + 4))
        $paused_font.Dispose()
        $paused_brush.Dispose()
    }

    $background_brush.Dispose()
    $frame_pen.Dispose()
    $grid_pen.Dispose()
    $zero_pen.Dispose()
    $axis_font.Dispose()
    $label_brush.Dispose()
    $text_brush.Dispose()
}

function set_wave_xy_mode {
    # XY 模式开关：重建余晖累积层（含最近窗口回铺）并刷新按钮态
    param([bool]$on)

    if ($script:wave_xy_on -eq $on) { return }
    $script:wave_xy_on = $on
    if ($script:wbutton_xy -and -not $script:wbutton_xy.IsDisposed) {
        set_wave_button_active -button $script:wbutton_xy -active $on
        $script:wbutton_xy.Text = $(if ($on) { 'XY 开' } else { 'XY 关' })
    }
    rebuild_wave_xy_accum
    if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
        $script:wave_panel.Invalidate()
    }
}

function refresh_wave_xy_combos {
    # XY 各窗口源下拉与通道列表同步（通道数变化/改名后由 ensure_wave_channel_checks 调）；
    # 项集合没变就只夹取已选项。程序化回设时抑制事件：通道逐个出现（append 每建一个
    # 通道调一次 ensure→refresh），count=1 时选择被临时钳位，不能写进期望状态。
    if (-not $script:wave_form -or $script:wave_form.IsDisposed) { return }
    $count = $script:wave_ch_values.Count
    if ($count -lt 1) { return }

    $names = New-Object 'System.Collections.Generic.List[string]'
    for ($i = 0; $i -lt $count; $i++) {
        [void]$names.Add((get_wave_channel_display_name -index $i))
    }
    $joined = ($names -join "`n")

    $script:wave_xy_syncing = $true
    try {
        for ($slot = 0; $slot -lt 4; $slot++) {
            $combo_x = $script:wcombo_xy_xs[$slot]
            $combo_y = $script:wcombo_xy_ys[$slot]
            if (-not $combo_x -or $combo_x.IsDisposed -or -not $combo_y -or $combo_y.IsDisposed) { continue }

            $current = ''
            foreach ($item in $combo_x.Items) { $current += [string]$item + "`n" }
            if ($current -ne $joined) {
                $combo_x.Items.Clear()
                $combo_y.Items.Clear()
                foreach ($name in $names) {
                    [void]$combo_x.Items.Add($name)
                    [void]$combo_y.Items.Add($name)
                }
            }

            $pair = get_wave_xy_pair -slot $slot
            if ($combo_x.SelectedIndex -ne $pair.X) { $combo_x.SelectedIndex = $pair.X }
            if ($combo_y.SelectedIndex -ne $pair.Y) { $combo_y.SelectedIndex = $pair.Y }
        }
    }
    finally {
        $script:wave_xy_syncing = $false
    }
}

function get_wave_xy_section_inner_width {
    if (-not $script:wave_channel_flow -or $script:wave_channel_flow.IsDisposed) { return 132 }
    return [Math]::Max(120, ($script:wave_channel_flow.ClientSize.Width - 16))
}

function sync_wave_panel_row_widths {
    # 通道面板宽度联动：通道行与 XY 控件区（含每窗口分组框）统一宽度，
    # ✎ 按钮靠 Anchor 右对齐，排版不随通道名文字长度变化
    if (-not $script:wave_channel_flow -or $script:wave_channel_flow.IsDisposed) { return }
    $inner = $script:wave_channel_flow.ClientSize.Width - 16
    if ($inner -lt 60) { return }
    foreach ($ctl in $script:wave_channel_flow.Controls) {
        if ($ctl -is [System.Windows.Forms.Panel]) {
            if ($ctl.Width -ne $inner) { $ctl.Width = $inner }
        }
    }
    if ($script:wave_xy_sep -and -not $script:wave_xy_sep.IsDisposed) {
        $script:wave_xy_sep.Width = $inner
    }
    foreach ($group in $script:wave_xy_groups) {
        if ($group -and -not $group.IsDisposed -and $group.Width -ne $inner) {
            $group.Width = $inner
        }
    }
}

function set_wave_xy_window_count {
    # 改窗口个数：显隐每窗口的参数分组框、调控件区高度、重建累积层
    param([int]$count)

    if ($count -ne 1 -and $count -ne 2 -and $count -ne 4) { $count = 2 }
    $script:wave_xy_count = $count
    for ($slot = 0; $slot -lt 4; $slot++) {
        $group = $script:wave_xy_groups[$slot]
        if ($group -and -not $group.IsDisposed) {
            $group.Visible = ($slot -lt $count)
        }
    }
    if ($script:wave_xy_section -and -not $script:wave_xy_section.IsDisposed) {
        $script:wave_xy_section.Height = 112 + $count * 146 + 6
    }
    rebuild_wave_xy_accum
    if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
        $script:wave_panel.Invalidate()
    }
}

function ensure_wave_xy_section {
    # 通道面板底部的 XY 相位图控制区（RIGOL 式多窗口）。
    # reset_wave_buffers 会清空通道面板，由 ensure_wave_channel_checks 在每次通道行
    # 重建后调用：控件只建一次，之后强制挪回面板末尾（同一实例重复挂载不重建，选择不丢）。
    # 布局用固定坐标两列栅格（标签列 + 控件列），不受通道名文字长度影响。
    if (-not $script:wave_form -or $script:wave_form.IsDisposed) { return }
    if (-not $script:wave_channel_flow -or $script:wave_channel_flow.IsDisposed) { return }

    if ($script:wave_xy_section -and -not $script:wave_xy_section.IsDisposed) {
        refresh_wave_xy_combos
        # 新通道行是逐个追加的（append_wave_frame 每建一行调一次本函数），
        # section 若已挂会卡在行中间 → 每次都强制挪到面板末尾
        $flow_controls = $script:wave_channel_flow.Controls
        if ($flow_controls[$flow_controls.Count - 1] -ne $script:wave_xy_section) {
            $flow_controls.SetChildIndex($script:wave_xy_section, ($flow_controls.Count - 1))
        }
        sync_wave_panel_row_widths
        return
    }

    $wave_input_bg = [System.Drawing.Color]::FromArgb(38, 45, 58)
    $wave_text_fg = [System.Drawing.Color]::FromArgb(225, 230, 240)
    $wave_dim_fg = [System.Drawing.Color]::FromArgb(150, 158, 172)
    $sep_color = [System.Drawing.Color]::FromArgb(58, 66, 84)

    $inner = get_wave_xy_section_inner_width
    $section = New-Object System.Windows.Forms.Panel
    $section.Width = $inner
    $section.Height = 112 + (get_wave_xy_count) * 146 + 6
    $section.Margin = New-Object System.Windows.Forms.Padding(0)
    $section.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $script:wave_xy_sep = New-Object System.Windows.Forms.Label
    $script:wave_xy_sep.Location = New-Object System.Drawing.Point(0, 6)
    $script:wave_xy_sep.Size = New-Object System.Drawing.Size($inner, 2)
    $script:wave_xy_sep.BackColor = $sep_color
    [void]$section.Controls.Add($script:wave_xy_sep)

    $header = New-Object System.Windows.Forms.Label
    $header.Text = 'XY 相位图（IQ 看相位）'
    $header.Location = New-Object System.Drawing.Point(0, 12)
    $header.AutoSize = $true
    $header.ForeColor = $wave_dim_fg
    [void]$section.Controls.Add($header)

    $script:wbutton_xy = New-Object System.Windows.Forms.Button
    $script:wbutton_xy.Text = 'XY 关'
    $script:wbutton_xy.Location = New-Object System.Drawing.Point(0, 32)
    $script:wbutton_xy.Size = New-Object System.Drawing.Size(62, 24)
    style_wave_button -button $script:wbutton_xy
    set_wave_button_active -button $script:wbutton_xy -active $script:wave_xy_on
    if ($script:wave_xy_on) { $script:wbutton_xy.Text = 'XY 开' }
    $script:wbutton_xy.Add_Click({
        set_wave_xy_mode (-not $script:wave_xy_on)
    })
    [void]$section.Controls.Add($script:wbutton_xy)

    # 全局参数（两列栅格）：窗口数、余晖 —— 每窗口自己的 X/Y/量程/分格在下方分组框里
    $grid_rows = @(
        @{ Label = '窗口数'; Y = 58 },
        @{ Label = '余晖'; Y = 84 }
    )
    foreach ($row_def in $grid_rows) {
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text = $row_def.Label
        $lbl.Location = New-Object System.Drawing.Point(0, ($row_def.Y + 4))
        $lbl.AutoSize = $true
        $lbl.ForeColor = $wave_dim_fg
        [void]$section.Controls.Add($lbl)
    }

    $script:wcombo_xy_count = New-Object DarkComboBox
    $script:wcombo_xy_count.DropDownStyle = 'DropDownList'
    $script:wcombo_xy_count.Location = New-Object System.Drawing.Point(40, 58)
    $script:wcombo_xy_count.Size = New-Object System.Drawing.Size(60, 24)
    [void]$script:wcombo_xy_count.Items.AddRange(@('1', '2', '4'))
    [void]$section.Controls.Add($script:wcombo_xy_count)

    $script:wcombo_xy_persist = New-Object DarkComboBox
    $script:wcombo_xy_persist.DropDownStyle = 'DropDownList'
    $script:wcombo_xy_persist.Location = New-Object System.Drawing.Point(40, 84)
    $script:wcombo_xy_persist.Size = New-Object System.Drawing.Size(60, 24)
    [void]$script:wcombo_xy_persist.Items.AddRange(@('关', '0.5s', '1s', '2s', '5s', '常驻'))
    [void]$section.Controls.Add($script:wcombo_xy_persist)

    # 每窗口一个带边框分组框：X 源 / Y 源 / 量程 / 分格 都属于该窗口，一眼可辨
    # 注意：New-Object 参数里「二元运算后跟逗号」会被 PS 吸进运算（op_Division 坑），
    # 所有算术先落变量再进构造参数
    for ($slot = 0; $slot -lt 4; $slot++) {
        $group_y = 112 + $slot * 146
        $group = New-Object System.Windows.Forms.Panel
        $group.Location = New-Object System.Drawing.Point(0, $group_y)
        $group.Size = New-Object System.Drawing.Size($inner, 138)
        $group.Margin = New-Object System.Windows.Forms.Padding(0)
        $group.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
        $group.Tag = $slot
        $group.Add_Paint({
            param($sender, $event_args)
            $border_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(70, 82, 105))
            try {
                $event_args.Graphics.DrawRectangle($border_pen, 0, 0, ($sender.Width - 1), ($sender.Height - 1))
            }
            finally {
                $border_pen.Dispose()
            }
        })
        [void]$group.Controls.Add((New-Object System.Windows.Forms.Label))
        $caption = $group.Controls[$group.Controls.Count - 1]
        $caption.Text = ('窗口 {0}' -f [string][char](0x2460 + $slot))   # 窗口 ①②③④
        $caption.Location = New-Object System.Drawing.Point(10, 6)
        $caption.AutoSize = $true
        $caption.ForeColor = [System.Drawing.Color]::FromArgb(180, 190, 205)

        $row_defs = @(
            @{ Label = 'X'; Name = 'x'; Y = 30 },
            @{ Label = 'Y'; Name = 'y'; Y = 56 },
            @{ Label = '量程'; Name = 'span'; Y = 82 },
            @{ Label = '分格'; Name = 'divs'; Y = 108 }
        )
        foreach ($row_def in $row_defs) {
            $lbl = New-Object System.Windows.Forms.Label
            $lbl.Text = $row_def.Label
            $lbl.Location = New-Object System.Drawing.Point(10, ($row_def.Y + 4))
            $lbl.AutoSize = $true
            $lbl.ForeColor = $wave_dim_fg
            [void]$group.Controls.Add($lbl)
        }

        $combo_x = New-Object DarkComboBox
        $combo_x.DropDownStyle = 'DropDownList'
        $combo_x.Location = New-Object System.Drawing.Point(48, 30)
        $combo_x.Size = New-Object System.Drawing.Size(66, 22)
        $combo_x.Tag = $slot
        [void]$group.Controls.Add($combo_x)

        $combo_y = New-Object DarkComboBox
        $combo_y.DropDownStyle = 'DropDownList'
        $combo_y.Location = New-Object System.Drawing.Point(48, 56)
        $combo_y.Size = New-Object System.Drawing.Size(66, 22)
        $combo_y.Tag = $slot
        [void]$group.Controls.Add($combo_y)

        $combo_span = New-Object DarkComboBox
        $combo_span.DropDownStyle = 'DropDownList'
        $combo_span.Location = New-Object System.Drawing.Point(48, 82)
        $combo_span.Size = New-Object System.Drawing.Size(66, 22)
        $combo_span.Tag = $slot
        [void]$combo_span.Items.AddRange(@('自适应', '±2', '±4', '±8', '±16', '±32'))
        [void]$group.Controls.Add($combo_span)

        $combo_divs = New-Object DarkComboBox
        $combo_divs.DropDownStyle = 'DropDownList'
        $combo_divs.Location = New-Object System.Drawing.Point(48, 108)
        $combo_divs.Size = New-Object System.Drawing.Size(66, 22)
        $combo_divs.Tag = $slot
        [void]$combo_divs.Items.AddRange(@('4', '5', '8', '10', '16'))
        [void]$group.Controls.Add($combo_divs)

        $script:wcombo_xy_xs[$slot] = $combo_x
        $script:wcombo_xy_ys[$slot] = $combo_y
        $script:wcombo_xy_span_boxes[$slot] = $combo_span
        $script:wcombo_xy_divs_boxes[$slot] = $combo_divs
        $script:wave_xy_groups[$slot] = $group
        [void]$section.Controls.Add($group)
    }

    foreach ($combo in @($script:wcombo_xy_count, $script:wcombo_xy_persist) + $script:wcombo_xy_xs + $script:wcombo_xy_ys + $script:wcombo_xy_span_boxes + $script:wcombo_xy_divs_boxes) {
        $combo.BackColor = $wave_input_bg
        $combo.ForeColor = $wave_text_fg
        $combo.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $combo.UseDark = $true
        $combo.ArrowBack = $wave_input_bg
        $combo.ArrowFore = $wave_text_fg
        $combo.BorderColor = $sep_color
    }
    $script:wcombo_xy_count.SelectedItem = [string](get_wave_xy_count)
    $script:wcombo_xy_persist.SelectedItem = [string]$script:wave_xy_persist
    if ($null -eq $script:wcombo_xy_persist.SelectedItem) { $script:wcombo_xy_persist.SelectedIndex = 2 }
    for ($slot = 0; $slot -lt 4; $slot++) {
        $script:wcombo_xy_span_boxes[$slot].SelectedItem = [string]$script:wave_xy_spans[$slot]
        if ($null -eq $script:wcombo_xy_span_boxes[$slot].SelectedItem) { $script:wcombo_xy_span_boxes[$slot].SelectedItem = '±8' }
        $script:wcombo_xy_divs_boxes[$slot].SelectedItem = [string](get_wave_xy_divisions -slot $slot)
    }

    $script:wcombo_xy_count.Add_SelectedIndexChanged({
        if ($script:wave_xy_syncing) { return }
        $value = 0
        if ([int]::TryParse([string]$script:wcombo_xy_count.SelectedItem, [ref]$value) -and $value -ne (get_wave_xy_count)) {
            set_wave_xy_window_count -count $value
        }
    })
    $script:wcombo_xy_persist.Add_SelectedIndexChanged({
        if ($script:wave_xy_syncing) { return }
        $value = [string]$script:wcombo_xy_persist.SelectedItem
        if ($value -and $value -ne $script:wave_xy_persist) {
            $script:wave_xy_persist = $value
            rebuild_wave_xy_accum
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })
    foreach ($combo in $script:wcombo_xy_span_boxes) {
        $combo.Add_SelectedIndexChanged({
            param($sender, $event_args)
            if ($script:wave_xy_syncing) { return }
            $slot = [int]$sender.Tag
            $value = [string]$sender.SelectedItem
            if ($value -and ([string]$script:wave_xy_spans[$slot]) -ne $value) {
                $script:wave_xy_spans[$slot] = $value
                # 量程切换让映射台阶自然变化 → 下一帧余晖按比例迁移，该窗口轨迹不清空
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        })
    }
    foreach ($combo in $script:wcombo_xy_divs_boxes) {
        $combo.Add_SelectedIndexChanged({
            param($sender, $event_args)
            if ($script:wave_xy_syncing) { return }
            $slot = [int]$sender.Tag
            $value = 0
            if ([int]::TryParse([string]$sender.SelectedItem, [ref]$value) -and $value -ne [int]$script:wave_xy_divs_arr[$slot]) {
                $script:wave_xy_divs_arr[$slot] = $value
                # 只改刻度密度，不改映射 → 余晖轨迹不动，该窗口网格立即重排
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        })
    }
    foreach ($combo in $script:wcombo_xy_xs) {
        $combo.Add_SelectedIndexChanged({
            param($sender, $event_args)
            if ($script:wave_xy_syncing) { return }
            $slot = [int]$sender.Tag
            if ($sender.SelectedIndex -ge 0 -and [int]$script:wave_xy_pairs[$slot * 2] -ne $sender.SelectedIndex) {
                $script:wave_xy_pairs[$slot * 2] = $sender.SelectedIndex
                rebuild_wave_xy_accum
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        })
    }
    foreach ($combo in $script:wcombo_xy_ys) {
        $combo.Add_SelectedIndexChanged({
            param($sender, $event_args)
            if ($script:wave_xy_syncing) { return }
            $slot = [int]$sender.Tag
            if ($sender.SelectedIndex -ge 0 -and [int]$script:wave_xy_pairs[$slot * 2 + 1] -ne $sender.SelectedIndex) {
                $script:wave_xy_pairs[$slot * 2 + 1] = $sender.SelectedIndex
                rebuild_wave_xy_accum
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        })
    }

    # ToolTip 挂在脚本变量上防 GC（控件不持有 ToolTip 强引用）
    $script:wave_xy_tooltip = New-Object System.Windows.Forms.ToolTip
    $script:wave_xy_tooltip.SetToolTip($script:wbutton_xy, 'XY 模式：每个窗口把 X 通道画横轴、Y 通道画纵轴，成相位轨迹（IQ 信号：I → X，Q → Y）')
    $script:wave_xy_tooltip.SetToolTip($script:wcombo_xy_count, 'XY 窗口个数（1/2/4）：2 = 左右双屏，4 = 2×2；格子保持正方形，IQ 正交性一眼可判')
    $script:wave_xy_tooltip.SetToolTip($script:wcombo_xy_persist, '余晖时间（全体窗口共用）：旧轨迹按此时间常数淡出；常驻 = 只累积不清；关 = 只画最新数据')
    foreach ($combo in $script:wcombo_xy_span_boxes) {
        $script:wave_xy_tooltip.SetToolTip($combo, '该窗口的 XY 量程：默认固定 ±8（两轴 -8~+8，中心 0，适合 IQ 星座图）；自适应 = 按数据范围自动缩放')
    }
    foreach ($combo in $script:wcombo_xy_divs_boxes) {
        $script:wave_xy_tooltip.SetToolTip($combo, '该窗口每轴的分格数（刻度随每格显示在窗内）')
    }
    foreach ($combo in $script:wcombo_xy_xs) {
        $script:wave_xy_tooltip.SetToolTip($combo, '该窗口的 X 轴（横轴）数据源通道')
    }
    foreach ($combo in $script:wcombo_xy_ys) {
        $script:wave_xy_tooltip.SetToolTip($combo, '该窗口的 Y 轴（纵轴）数据源通道；轨迹线用 Y 通道颜色')
    }

    $script:wave_xy_section = $section
    [void]$script:wave_channel_flow.Controls.Add($section)
    refresh_wave_xy_combos
    set_wave_xy_window_count -count (get_wave_xy_count)
    sync_wave_panel_row_widths
}


function start_wave_sim {
    if ($script:wave_sim_timer) {
        return
    }

    $script:wave_sim_t = 0
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 20
    $timer.Add_Tick({
        try {
            $t = $script:wave_sim_t
            $script:wave_sim_t++
            # 3 通道：带噪声正弦 / 余弦 / 调制拍频，按当前协议打包，走真实接收管线（含 RX 显示与日志）
            $noise = (Get-Random -Minimum -100 -Maximum 101) / 500.0
            $v0 = 2.5 * [Math]::Sin($t / 25.0) + $noise
            $v1 = 1.6 * [Math]::Cos($t / 40.0)
            $v2 = 3.0 * [Math]::Sin($t / 9.0) * [Math]::Cos($t / 70.0) + ((Get-Random -Minimum -30 -Maximum 31) / 100.0)

            switch ($script:wave_protocol) {
                'JustFloat' {
                    $frame_bytes = New-Object byte[] 16
                    [BitConverter]::GetBytes([float]$v0).CopyTo($frame_bytes, 0)
                    [BitConverter]::GetBytes([float]$v1).CopyTo($frame_bytes, 4)
                    [BitConverter]::GetBytes([float]$v2).CopyTo($frame_bytes, 8)
                    $jf_tail = [byte[]](0, 0, 0x80, 0x7F)
                    $jf_tail.CopyTo($frame_bytes, 12)
                    append_received_bytes -bytes $frame_bytes
                }
                'RawData' {
                    $chunk = New-Object byte[] 16
                    for ($k = 0; $k -lt 16; $k++) {
                        $sample = 127.0 + 100.0 * [Math]::Sin((($t * 16 + $k) / 12.0)) + (Get-Random -Minimum -15 -Maximum 16)
                        if ($sample -lt 0) { $sample = 0 }
                        if ($sample -gt 255) { $sample = 255 }
                        $chunk[$k] = [byte][int]$sample
                    }
                    append_received_bytes -bytes $chunk
                }
                default {
                    $line = ('{0:F4},{1:F4},{2:F4}' -f $v0, $v1, $v2)
                    append_received_bytes -bytes ([System.Text.Encoding]::UTF8.GetBytes($line + "`n"))
                }
            }
        }
        catch {
            write_runtime_log -message ('模拟数据异常: {0}' -f $_.Exception.Message)
        }
    })
    $script:wave_sim_timer = $timer
    $script:wave_sim_timer.Start()
    $script:wave_sim_on = $true
    if ($script:wbutton_sim -and -not $script:wbutton_sim.IsDisposed) {
        set_wave_button_active -button $script:wbutton_sim -active $true
        $script:wbutton_sim.Text = '■ 停止模拟'
    }
}

function stop_wave_sim {
    if ($script:wave_sim_timer) {
        try {
            $script:wave_sim_timer.Stop()
            $script:wave_sim_timer.Dispose()
        }
        catch {
        }
        $script:wave_sim_timer = $null
    }
    $script:wave_sim_on = $false
    if ($script:wbutton_sim -and -not $script:wbutton_sim.IsDisposed) {
        set_wave_button_active -button $script:wbutton_sim -active $false
        $script:wbutton_sim.Text = '▶ 模拟数据'
    }
}

function export_wave_to_file {
    param(
        [string]$path,
        [ValidateSet('png', 'csv', 'txt')]
        [string]$format,
        [int[]]$channel_indices
    )

    if (-not $channel_indices -or $channel_indices.Count -eq 0) {
        throw '未选择导出通道。'
    }

    $culture = [System.Globalization.CultureInfo]::InvariantCulture

    if ($format -eq 'png') {
        if (-not $script:wave_panel -or $script:wave_panel.IsDisposed) {
            throw '波形窗口已关闭，无法导出图片。'
        }

        $enabled_override = New-Object bool[] $script:wave_ch_values.Count
        foreach ($index in $channel_indices) {
            if ($index -ge 0 -and $index -lt $script:wave_ch_values.Count) {
                $enabled_override[$index] = $true
            }
        }

        # 2 倍分辨率渲染当前视图（draw 按传入尺寸重绘，放大不失真）
        $scale = 2
        $bitmap = New-Object System.Drawing.Bitmap(($script:wave_panel.ClientSize.Width * $scale), ($script:wave_panel.ClientSize.Height * $scale))
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                if ($script:wave_xy_on) {
                    # XY 模式导出相位图：跳过累积层全量直绘
                    draw_wave_xy_graphics -graphics $graphics -width $bitmap.Width -height $bitmap.Height -direct
                }
                else {
                    draw_wave_graphics -graphics $graphics -width $bitmap.Width -height $bitmap.Height -enabled_override $enabled_override
                }
            }
            finally {
                $graphics.Dispose()
            }
            $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $bitmap.Dispose()
        }
        return
    }

    # csv / txt：仅导出选中通道
    $separator = ','
    if ($format -eq 'txt') {
        $separator = "`t"
    }

    $writer = [System.IO.StreamWriter]::new($path, $false, [System.Text.UTF8Encoding]::new($false))
    try {
        if ($format -eq 'txt') {
            $writer.WriteLine('# RK3562 串口收发工具 V6 波形导出')
            $writer.WriteLine(('# 时间: {0}  通道: {1}  帧数: {2}' -f (get_timestamp_text), (($channel_indices | ForEach-Object { get_wave_channel_display_name -index $_ }) -join ','), $script:wave_frame_count))
        }

        $header = ($channel_indices | ForEach-Object { get_wave_channel_display_name -index $_ }) -join $separator
        $writer.WriteLine('index' + $separator + $header)

        $builder = New-Object System.Text.StringBuilder
        for ($row_index = 0; $row_index -lt $script:wave_frame_count; $row_index++) {
            [void]$builder.Append($row_index)
            foreach ($index in $channel_indices) {
                [void]$builder.Append($separator)
                [void]$builder.Append($script:wave_ch_values[$index][$row_index].ToString('R', $culture))
            }
            $writer.WriteLine($builder.ToString())
            [void]$builder.Clear()
        }
    }
    finally {
        $writer.Dispose()
    }
}

function get_wave_export_settings_path {
    Join-Path -Path $script:app_root -ChildPath 'wave_export_default.txt'
}

function get_wave_export_default_dir {
    $settings_path = get_wave_export_settings_path
    if (Test-Path -LiteralPath $settings_path) {
        try {
            $saved = (Get-Content -LiteralPath $settings_path -TotalCount 1 -Encoding UTF8).Trim()
            if ($saved -and (Test-Path -LiteralPath $saved)) {
                return $saved
            }
        }
        catch {
        }
    }
    return $null
}

function set_wave_export_default_dir {
    param([string]$directory)

    try {
        [System.IO.File]::WriteAllText((get_wave_export_settings_path), $directory, [System.Text.UTF8Encoding]::new($false))
    }
    catch {
        write_runtime_log -message ('保存默认导出路径失败: {0}' -f $_.Exception.Message)
    }
}

function show_wave_export_dialog {
    if ($script:wave_ch_values.Count -eq 0 -or $script:wave_frame_count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show('暂无波形数据。', '导出', 'OK', 'Information') | Out-Null
        return
    }

    $light_fore = [System.Drawing.Color]::FromArgb(205, 212, 224)
    $gray_fore = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'

    $script:exdlg_channel_checks = New-Object 'System.Collections.Generic.List[object]'
    $script:exdlg_last_file = ''
    $script:exdlg_form = New-Object System.Windows.Forms.Form
    $script:exdlg_form.Text = '导出波形数据'
    $script:exdlg_form.StartPosition = 'CenterParent'
    $script:exdlg_form.Size = New-Object System.Drawing.Size(400, 524)
    $script:exdlg_form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $script:exdlg_form.MaximizeBox = $false
    $script:exdlg_form.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $script:exdlg_form.Font = new_font -size 9

    $exlabel_type = New-Object System.Windows.Forms.Label
    $exlabel_type.Text = '导出类型'
    $exlabel_type.Location = New-Object System.Drawing.Point(16, 16)
    $exlabel_type.AutoSize = $true
    $exlabel_type.ForeColor = $gray_fore

    $script:exdlg_radio_png = New-Object System.Windows.Forms.RadioButton
    $script:exdlg_radio_png.Text = 'PNG 图片（当前波形视图，2 倍分辨率）'
    $script:exdlg_radio_png.Location = New-Object System.Drawing.Point(36, 42)
    $script:exdlg_radio_png.AutoSize = $true
    $script:exdlg_radio_png.ForeColor = $light_fore

    $script:exdlg_radio_csv = New-Object System.Windows.Forms.RadioButton
    $script:exdlg_radio_csv.Text = 'CSV 数据（逗号分隔，适合 Excel）'
    $script:exdlg_radio_csv.Location = New-Object System.Drawing.Point(36, 70)
    $script:exdlg_radio_csv.AutoSize = $true
    $script:exdlg_radio_csv.ForeColor = $light_fore
    $script:exdlg_radio_csv.Checked = $true

    $script:exdlg_radio_txt = New-Object System.Windows.Forms.RadioButton
    $script:exdlg_radio_txt.Text = 'TXT 数据（制表符分隔，便于阅读）'
    $script:exdlg_radio_txt.Location = New-Object System.Drawing.Point(36, 98)
    $script:exdlg_radio_txt.AutoSize = $true
    $script:exdlg_radio_txt.ForeColor = $light_fore

    $exlabel_channel = New-Object System.Windows.Forms.Label
    $exlabel_channel.Text = '导出通道'
    $exlabel_channel.Location = New-Object System.Drawing.Point(16, 136)
    $exlabel_channel.AutoSize = $true
    $exlabel_channel.ForeColor = $gray_fore

    $script:exdlg_flow = New-Object System.Windows.Forms.FlowLayoutPanel
    $script:exdlg_flow.Location = New-Object System.Drawing.Point(16, 160)
    $script:exdlg_flow.Size = New-Object System.Drawing.Size(352, 140)
    $script:exdlg_flow.FlowDirection = 'LeftToRight'
    $script:exdlg_flow.WrapContents = $true
    $script:exdlg_flow.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)

    for ($i = 0; $i -lt $script:wave_ch_values.Count; $i++) {
        $checkbox = New-Object DarkCheckBox
        $checkbox.Text = get_wave_channel_display_name -index $i
        $checkbox.AutoSize = $true
        $checkbox.Checked = $true
        $checkbox.ForeColor = (get_wave_channel_color -index $i)
        $checkbox.Margin = New-Object System.Windows.Forms.Padding(6, 6, 6, 2)
        [void]$script:exdlg_channel_checks.Add($checkbox)
        [void]$script:exdlg_flow.Controls.Add($checkbox)
    }

    $script:exdlg_button_allch = New-Object System.Windows.Forms.Button
    $script:exdlg_button_allch.Text = '全不选'
    $script:exdlg_button_allch.Location = New-Object System.Drawing.Point(16, 312)
    $script:exdlg_button_allch.Size = New-Object System.Drawing.Size(76, 28)
    style_wave_button -button $script:exdlg_button_allch
    $script:exdlg_button_allch.Add_Click({
        $any_unchecked = $false
        foreach ($checkbox in $script:exdlg_channel_checks) {
            if (-not $checkbox.Checked) {
                $any_unchecked = $true
                break
            }
        }

        if ($any_unchecked) {
            foreach ($checkbox in $script:exdlg_channel_checks) { $checkbox.Checked = $true }
            $script:exdlg_button_allch.Text = '全不选'
        }
        else {
            foreach ($checkbox in $script:exdlg_channel_checks) { $checkbox.Checked = $false }
            $script:exdlg_button_allch.Text = '全选'
        }
    })

    $exlabel_default = New-Object System.Windows.Forms.Label
    $exlabel_default.Text = '默认路径（导出成功后自动记忆）'
    $exlabel_default.Location = New-Object System.Drawing.Point(16, 350)
    $exlabel_default.AutoSize = $true
    $exlabel_default.ForeColor = $gray_fore

    $script:exdlg_text_default = New-Object System.Windows.Forms.TextBox
    $script:exdlg_text_default.Location = New-Object System.Drawing.Point(16, 368)
    $script:exdlg_text_default.Size = New-Object System.Drawing.Size(352, 22)
    $script:exdlg_text_default.ReadOnly = $true
    $script:exdlg_text_default.BorderStyle = 'None'
    $script:exdlg_text_default.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $script:exdlg_text_default.ForeColor = $gray_fore

    $exlabel_result = New-Object System.Windows.Forms.Label
    $exlabel_result.Text = '导出结果'
    $exlabel_result.Location = New-Object System.Drawing.Point(16, 398)
    $exlabel_result.AutoSize = $true
    $exlabel_result.ForeColor = $gray_fore

    $script:exdlg_text_result = New-Object System.Windows.Forms.TextBox
    $script:exdlg_text_result.Location = New-Object System.Drawing.Point(16, 416)
    $script:exdlg_text_result.Size = New-Object System.Drawing.Size(352, 22)
    $script:exdlg_text_result.ReadOnly = $true
    $script:exdlg_text_result.BorderStyle = 'None'
    $script:exdlg_text_result.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $script:exdlg_text_result.ForeColor = $light_fore

    $script:exdlg_button_export = New-Object System.Windows.Forms.Button
    $script:exdlg_button_export.Text = '导出...'
    $script:exdlg_button_export.Location = New-Object System.Drawing.Point(16, 448)
    $script:exdlg_button_export.Size = New-Object System.Drawing.Size(84, 30)
    style_wave_button -button $script:exdlg_button_export
    $script:exdlg_button_export.Add_Click({
        $any_selected = $false
        foreach ($checkbox in $script:exdlg_channel_checks) {
            if ($checkbox.Checked) {
                $any_selected = $true
                break
            }
        }
        if (-not $any_selected) {
            [System.Windows.Forms.MessageBox]::Show('请至少选择一个通道。', '导出', 'OK', 'Warning') | Out-Null
            return
        }

        try {
            $selected_indices = @()
            for ($i = 0; $i -lt $script:exdlg_channel_checks.Count; $i++) {
                if ($script:exdlg_channel_checks[$i].Checked) {
                    $selected_indices += $i
                }
            }

            $format = 'csv'
            $filter_index = 2
            if ($script:exdlg_radio_png.Checked) {
                $format = 'png'
                $filter_index = 1
            }
            elseif ($script:exdlg_radio_txt.Checked) {
                $format = 'txt'
                $filter_index = 3
            }

            $handler_log_dir = Join-Path -Path $script:app_root -ChildPath 'logs'
            if (-not (Test-Path -LiteralPath $handler_log_dir)) {
                New-Item -ItemType Directory -Path $handler_log_dir | Out-Null
            }

            $initial_dir = get_wave_export_default_dir
            if (-not $initial_dir) {
                $initial_dir = $handler_log_dir
            }

            $dialog = New-Object System.Windows.Forms.SaveFileDialog
            $dialog.Filter = 'PNG 图片 (*.png)|*.png|CSV 数据 (*.csv)|*.csv|TXT 数据 (*.txt)|*.txt'
            $dialog.FilterIndex = $filter_index
            $dialog.FileName = 'wave_export_{0}' -f (Get-Date -Format 'yyyyMMdd_HHmmss')
            $dialog.InitialDirectory = $initial_dir
            if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
                return
            }

            export_wave_to_file -path $dialog.FileName -format $format -channel_indices $selected_indices
            $exported_dir = Split-Path -Path $dialog.FileName -Parent
            set_wave_export_default_dir -directory $exported_dir

            $script:exdlg_last_file = $dialog.FileName
            $script:exdlg_text_default.Text = $exported_dir
            $script:exdlg_text_result.Text = $dialog.FileName
            $script:exdlg_button_open.Enabled = $true

            add_message_entry -direction 'SYS' -text ('波形已导出({0}, 通道 {1}): {2}' -f $format.ToUpper(), (($selected_indices | ForEach-Object { get_wave_channel_display_name -index $_ }) -join ','), $dialog.FileName)
        }
        catch {
            write_runtime_log -message ('导出波形失败: {0}' -f $_.Exception.Message)
            [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '导出失败', 'OK', 'Error') | Out-Null
        }
    })

    $script:exdlg_button_open = New-Object System.Windows.Forms.Button
    $script:exdlg_button_open.Text = '打开文件夹'
    $script:exdlg_button_open.Location = New-Object System.Drawing.Point(108, 448)
    $script:exdlg_button_open.Size = New-Object System.Drawing.Size(96, 30)
    $script:exdlg_button_open.Enabled = $false
    style_wave_button -button $script:exdlg_button_open
    $script:exdlg_button_open.Add_Click({
        if ($script:exdlg_last_file -and (Test-Path -LiteralPath $script:exdlg_last_file)) {
            Start-Process explorer.exe -ArgumentList ('/select,"{0}"' -f $script:exdlg_last_file)
        }
    })

    $script:exdlg_button_close = New-Object System.Windows.Forms.Button
    $script:exdlg_button_close.Text = '关闭'
    $script:exdlg_button_close.Location = New-Object System.Drawing.Point(292, 448)
    $script:exdlg_button_close.Size = New-Object System.Drawing.Size(84, 30)
    style_wave_button -button $script:exdlg_button_close
    $script:exdlg_button_close.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $script:exdlg_form.CancelButton = $script:exdlg_button_close

    $default_dir = get_wave_export_default_dir
    if ($default_dir) {
        $script:exdlg_text_default.Text = $default_dir
    }
    else {
        $script:exdlg_text_default.Text = $log_dir + '（尚未记忆，首次导出时选择）'
    }

    $script:exdlg_form.Controls.AddRange(@(
        $exlabel_type,
        $script:exdlg_radio_png,
        $script:exdlg_radio_csv,
        $script:exdlg_radio_txt,
        $exlabel_channel,
        $script:exdlg_flow,
        $script:exdlg_button_allch,
        $exlabel_default,
        $script:exdlg_text_default,
        $exlabel_result,
        $script:exdlg_text_result,
        $script:exdlg_button_export,
        $script:exdlg_button_open,
        $script:exdlg_button_close
    ))

    [void]$script:exdlg_form.ShowDialog($script:wave_form)

    $script:exdlg_form.Dispose()
    $script:exdlg_form = $null
    $script:exdlg_channel_checks = $null
    $script:exdlg_last_file = $null
}

function draw_wave_graphics {
    param(
        [System.Drawing.Graphics]$graphics,
        [int]$width,
        [int]$height,
        [bool[]]$enabled_override = $null
    )

    # 背景微渐变：扫掉死黑（上亮下暗 3% 亮度差）
    $background_brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.Point(0, 0)),
        (New-Object System.Drawing.Point(0, $height)),
        $script:theme.PlotBgTop,
        $script:theme.PlotBgBottom)
    $graphics.FillRectangle($background_brush, 0, 0, $width, $height)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $plot_left = $script:wave_plot_left
    $plot_top = $script:wave_plot_top
    $plot_right = $script:wave_plot_right
    $plot_bottom = $script:wave_plot_bottom
    $plot_width = $width - $plot_left - $plot_right
    $plot_height = $height - $plot_top - $plot_bottom
    if ($plot_width -lt 20 -or $plot_height -lt 20) {
        $background_brush.Dispose()
        return
    }

    $frames = $null
    $total = $script:wave_frame_count
    $channel_count = $script:wave_ch_values.Count

    $checks = $script:wave_channel_checks
    $enabled = New-Object bool[] $channel_count
    $any_enabled = $false
    for ($c = 0; $c -lt $channel_count; $c++) {
        if ($enabled_override -and $enabled_override.Count -gt $c) {
            # 导出 PNG 时用调用方指定的通道可见性
            if ($enabled_override[$c]) {
                $enabled[$c] = $true
                $any_enabled = $true
            }
        }
        elseif ($checks.Count -le $c -or $checks[$c].Checked) {
            $enabled[$c] = $true
            $any_enabled = $true
        }
    }

    # Y 轴范围（自动/手动逻辑统一在 get_wave_y_range，滚轮缩放也复用）
    $y_range_result = get_wave_y_range -enabled $enabled -total $total -channel_count $channel_count
    $y_min = $y_range_result.Min
    $y_max = $y_range_result.Max
    $y_range = $y_max - $y_min
    $y_scale = $plot_height / $y_range

    $grid_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 55, 70))
    $axis_font = new_font -size 8.25

    # 绘图区描边框（区分绘图区与面板背景）
    $frame_pen = New-Object System.Drawing.Pen($script:theme.PlotFrame)
    $graphics.DrawRectangle($frame_pen, [float]$plot_left, [float]$plot_top, [float]$plot_width, [float]$plot_height)
    $frame_pen.Dispose()
    $label_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(168, 178, 195))
    $text_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(215, 222, 235))

    # 水平网格与 Y 标签
    $y_step = get_nice_step -range $y_range -target_lines 6
    $grid_guard = 0
    $y_value = [Math]::Ceiling($y_min / $y_step) * $y_step
    while (($y_value -le ($y_max + $y_step * 0.001)) -and ($grid_guard -lt 64)) {
        $yy = $plot_top + ($y_max - $y_value) * $y_scale
        $graphics.DrawLine($grid_pen, $plot_left, [float]$yy, ($plot_left + $plot_width), [float]$yy)
        $graphics.DrawString($y_value.ToString('G5'), $axis_font, $label_brush, 4, [float]($yy - 7))
        $y_value += $y_step
        $grid_guard++
    }

    # 垂直网格与 X 标签（采样帧序号）
    $view = get_wave_view_range -total $total
    $visible_count = $view.End - $view.Start
    if ($total -gt 1 -and $visible_count -gt 1) {
        $span = $visible_count - 1
        $first_abs = $view.Start
        $x_step = get_nice_step -range $span -target_lines 8
        if ($x_step -lt 1) { $x_step = 1 }
        $tick = [Math]::Ceiling($first_abs / $x_step) * $x_step
        $tick_guard = 0
        while ($tick_guard -lt 64) {
            $j = $tick - $first_abs
            if ($j -gt $span) { break }
            if ($j -ge 0) {
                $xx = $plot_left + ($j / $span) * $plot_width
                $graphics.DrawLine($grid_pen, [float]$xx, $plot_top, [float]$xx, ($plot_top + $plot_height))
                $graphics.DrawString([int][Math]::Round($tick), $axis_font, $label_brush, [float]($xx - 8), [float]($plot_top + $plot_height + 4))
            }
            $tick += $x_step
            $tick_guard++
        }
    }

    # 通道曲线
    if ($total -gt 0 -and $any_enabled) {
        $start_index = $view.Start
        for ($c = 0; $c -lt $channel_count; $c++) {
            if (-not $enabled[$c]) {
                continue
            }

            $ch_color = get_wave_channel_color -index $c
            $pen = New-Object System.Drawing.Pen($ch_color)
            $pen.Width = 1.8
            $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
            $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
            $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            # 曲线下方低透明度同色填充（多通道叠加也可读）
            $fill_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(26, $ch_color.R, $ch_color.G, $ch_color.B))
            $plot_bottom_y = $plot_top + $plot_height

            if ($visible_count -le $plot_width) {
                # 点数不超过像素列数：全采样，按连续有效段绘制
                $run = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
                $view_data = get_wave_view_data -channel $c -start $view.Start -end $view.End
                $channel_list = $view_data.Data
                for ($j = 0; $j -lt $visible_count; $j++) {
                    $v = $channel_list[$start_index + $j]
                    if ([double]::IsNaN($v) -or [double]::IsInfinity($v)) {
                        if ($run.Count -gt 1) {
                            $fill_poly = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
                            $fill_poly.AddRange($run.ToArray())
                            $fill_poly.Add([System.Drawing.PointF]::new([float]$run[$run.Count - 1].X, [float]$plot_bottom_y))
                            $fill_poly.Add([System.Drawing.PointF]::new([float]$run[0].X, [float]$plot_bottom_y))
                            $graphics.FillPolygon($fill_brush, $fill_poly.ToArray())
                            $graphics.DrawLines($pen, $run.ToArray())
                        }
                        elseif ($run.Count -eq 1) {
                            $point = $run[0]
                            $graphics.FillRectangle($pen.Brush, $point.X - 1, $point.Y - 1, 2.5, 2.5)
                        }
                        $run.Clear()
                        continue
                    }

                    if ($visible_count -gt 1) {
                        $x = $plot_left + $j * ($plot_width / ($visible_count - 1))
                    }
                    else {
                        $x = $plot_left + $plot_width / 2.0
                    }
                    $y = $plot_top + ($y_max - $v) * $y_scale
                    if ($y -lt $plot_top) { $y = $plot_top }
                    elseif ($y -gt ($plot_top + $plot_height)) { $y = $plot_top + $plot_height }
                    $run.Add([System.Drawing.PointF]::new([float]$x, [float]$y))
                }
                if ($run.Count -gt 1) {
                    $fill_poly = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
                    $fill_poly.AddRange($run.ToArray())
                    $fill_poly.Add([System.Drawing.PointF]::new([float]$run[$run.Count - 1].X, [float]$plot_bottom_y))
                    $fill_poly.Add([System.Drawing.PointF]::new([float]$run[0].X, [float]$plot_bottom_y))
                    $graphics.FillPolygon($fill_brush, $fill_poly.ToArray())
                    $graphics.DrawLines($pen, $run.ToArray())
                }
                elseif ($run.Count -eq 1) {
                    $point = $run[0]
                    $graphics.FillRectangle($pen.Brush, $point.X - 1, $point.Y - 1, 2.5, 2.5)
                }
            }
            else {
                # 每像素列取 min/max 抽稀，保留尖峰
                $view_data = get_wave_view_data -channel $c -start $view.Start -end $view.End
                $channel_list = $view_data.Data
                $points = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
                $bucket_size = $visible_count / [double]$plot_width
                for ($col = 0; $col -lt $plot_width; $col++) {
                    $j0 = $start_index + [int][Math]::Floor($col * $bucket_size)
                    $j1 = $start_index + [int][Math]::Floor(($col + 1) * $bucket_size) - 1
                    if ($j1 -lt $j0) { $j1 = $j0 }
                    if ($j0 -gt ($total - 1)) { break }

                    $col_min = [double]::PositiveInfinity
                    $col_max = [double]::NegativeInfinity
                    $has_value = $false
                    for ($j = $j0; $j -le $j1; $j++) {
                        $v = $channel_list[$j]
                        if ([double]::IsNaN($v) -or [double]::IsInfinity($v)) {
                            continue
                        }
                        if ($v -lt $col_min) { $col_min = $v }
                        if ($v -gt $col_max) { $col_max = $v }
                        $has_value = $true
                    }
                    if (-not $has_value) {
                        continue
                    }

                    $x = $plot_left + $col + 0.5
                    $y_min_px = $plot_top + ($y_max - $col_max) * $y_scale
                    $y_max_px = $plot_top + ($y_max - $col_min) * $y_scale
                    if ($y_min_px -lt $plot_top) { $y_min_px = $plot_top }
                    if ($y_min_px -gt ($plot_top + $plot_height)) { $y_min_px = $plot_top + $plot_height }
                    if ($y_max_px -lt $plot_top) { $y_max_px = $plot_top }
                    if ($y_max_px -gt ($plot_top + $plot_height)) { $y_max_px = $plot_top + $plot_height }
                    $points.Add([System.Drawing.PointF]::new([float]$x, [float]$y_min_px))
                    $points.Add([System.Drawing.PointF]::new([float]$x, [float]$y_max_px))
                }
                if ($points.Count -gt 1) {
                    $fill_poly = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
                    $fill_poly.AddRange($points.ToArray())
                    $fill_poly.Add([System.Drawing.PointF]::new([float]$points[$points.Count - 1].X, [float]$plot_bottom_y))
                    $fill_poly.Add([System.Drawing.PointF]::new([float]$points[0].X, [float]$plot_bottom_y))
                    $graphics.FillPolygon($fill_brush, $fill_poly.ToArray())
                    $graphics.DrawLines($pen, $points.ToArray())
                }
            }
            $fill_brush.Dispose()
            $pen.Dispose()
        }

        # 图例：通道色标 + 最新值
        $legend_x = $plot_left + 10
        $legend_y = $plot_top + 6
        $legend_font = new_font -size 8.5
        for ($c = 0; $c -lt $channel_count -and $c -lt 16; $c++) {
            if (-not $enabled[$c]) {
                continue
            }
            $color = get_wave_channel_color -index $c
            $swatch = New-Object System.Drawing.SolidBrush($color)
            $graphics.FillRectangle($swatch, $legend_x, $legend_y + 3, 10, 10)
            $swatch.Dispose()
            $latest = 0.0
            if ($c -lt $script:wave_channel_last.Count) {
                $latest = $script:wave_channel_last[$c]
            }
            $graphics.DrawString(('{0}  {1}' -f (get_wave_channel_display_name -index $c), $latest.ToString('G6')), $legend_font, $text_brush, ($legend_x + 16), $legend_y)
            $legend_y += 17
        }
        $legend_font.Dispose()
    }
    else {
        $hint_font = new_font -size 10
        $hint_text = '等待 FireWater 数据行（每行: ch0,ch1,...  以换行结束），如: 1.23,4.56'
        $size_text = $graphics.MeasureString($hint_text, $hint_font)
        $graphics.DrawString($hint_text, $hint_font, $label_brush, [float](($width - $size_text.Width) / 2), [float](($height - $size_text.Height) / 2))
        $hint_font.Dispose()
    }

    if ($script:wave_paused) {
        $paused_font = new_font -size 9 -style Bold
        $paused_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 120, 110))
        $graphics.DrawString('已暂停', $paused_font, $paused_brush, ($width - 72), ($plot_top + 4))
        $paused_font.Dispose()
        $paused_brush.Dispose()
    }

    # 十字光标：竖线吸附最近帧，提示框显示帧号与各可见通道该帧数值
    if ($script:wave_crosshair -and $total -gt 0 -and $view.End -gt $view.Start) {
        $cx = [double]$script:wave_crosshair.Px
        $cy = [double]$script:wave_crosshair.Py
        $frame_idx = $view.Start
        $snap_x = [double]$plot_left
        if (($view.End - $view.Start) -gt 1) {
            $px_per_frame = $plot_width / ($view.End - $view.Start - 1)
            $frame_float = $view.Start + ($cx - $plot_left) / $px_per_frame
            $frame_idx = [int][Math]::Round($frame_float)
            if ($frame_idx -lt $view.Start) { $frame_idx = $view.Start }
            if ($frame_idx -gt ($view.End - 1)) { $frame_idx = $view.End - 1 }
            $snap_x = $plot_left + ($frame_idx - $view.Start) * $px_per_frame
        }

        $cross_pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(150, 205, 225))
        $cross_pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
        $graphics.DrawLine($cross_pen, [float]$snap_x, [float]$plot_top, [float]$snap_x, [float]($plot_top + $plot_height))
        $graphics.DrawLine($cross_pen, [float]$plot_left, [float]$cy, [float]($plot_left + $plot_width), [float]$cy)
        $cross_pen.Dispose()

        $tip_lines = New-Object 'System.Collections.Generic.List[string]'
        [void]$tip_lines.Add(('帧 {0}' -f $frame_idx))
        for ($c = 0; $c -lt $channel_count -and $c -lt 16; $c++) {
            if (-not $enabled[$c]) {
                continue
            }
            $v = [double]::NaN
            if ($c -lt $script:wave_ch_values.Count -and $frame_idx -ge 0) {
                $channel_list_c = $script:wave_ch_values[$c]
                if ($frame_idx -lt $channel_list_c.Count) {
                    $v = $channel_list_c[$frame_idx]
                }
            }
            if ([double]::IsNaN($v) -or [double]::IsInfinity($v)) {
                [void]$tip_lines.Add(('{0} = --' -f (get_wave_channel_display_name -index $c)))
            }
            else {
                [void]$tip_lines.Add(('{0} = {1}' -f (get_wave_channel_display_name -index $c), $v.ToString('G6')))
            }
        }

        $tip_font = new_font -size 8.25
        $line_height = 15
        $box_width = 0
        foreach ($tip_line in $tip_lines) {
            $line_size = $graphics.MeasureString($tip_line, $tip_font)
            if ($line_size.Width -gt $box_width) { $box_width = $line_size.Width }
        }
        $box_height = $tip_lines.Count * $line_height + 8
        $box_x = $cx + 14
        if ($box_x + $box_width + 4 -gt ($plot_left + $plot_width)) { $box_x = $cx - 14 - $box_width }
        $box_y = $cy + 12
        if ($box_y + $box_height + 4 -gt ($plot_top + $plot_height)) { $box_y = $cy - 12 - $box_height }
        if ($box_x -lt $plot_left) { $box_x = $plot_left }
        if ($box_y -lt $plot_top) { $box_y = $plot_top }

        $tip_bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(195, 30, 36, 50))
        $graphics.FillRectangle($tip_bg, [float]$box_x, [float]$box_y, [float]($box_width + 8), [float]$box_height)
        $tip_bg.Dispose()
        $tip_y = $box_y + 4
        foreach ($tip_line in $tip_lines) {
            $graphics.DrawString($tip_line, $tip_font, $text_brush, [float]($box_x + 4), [float]$tip_y)
            $tip_y += $line_height
        }
        $tip_font.Dispose()
    }

    $background_brush.Dispose()
    $grid_pen.Dispose()
    $axis_font.Dispose()
    $label_brush.Dispose()
    $text_brush.Dispose()
}

function apply_wave_y_manual {
    $min_value = 0.0
    $max_value = 0.0
    $ok_min = [double]::TryParse($script:wtext_ymin.Text.Trim(), [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$min_value)
    $ok_max = [double]::TryParse($script:wtext_ymax.Text.Trim(), [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$max_value)
    if (-not ($ok_min -and $ok_max)) {
        return
    }

    if ($max_value -le $min_value) {
        $max_value = $min_value + 1.0
    }
    $script:wave_y_min = $min_value
    $script:wave_y_max = $max_value

    if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
        $script:wave_panel.Invalidate()
    }
}

function get_ai_format_doc {
    # 生成给 AI 看的单协议数据格式说明（一键复制用）
    param([string]$protocol)

    switch ($protocol) {
        'JustFloat' {
            return @'
请让单片机/设备按 JustFloat 二进制协议输出数据，我的串口工具（兼容 VOFA+ JustFloat）会实时绘制波形：
- 每帧 = N 个 float32（小端）+ 4 字节帧尾 0x00 0x00 0x80 0x7F
- 通道数 = 帧长/4，可以逐帧变化；帧尾丢失会自动重新同步
- 适合高采样率多通道，正文无需任何文本
- C 示例（2 通道）：
  float ch[2] = { value0, value1 };
  uint8_t tail[4] = { 0x00, 0x00, 0x80, 0x7F };
  Serial.write((uint8_t *)ch, sizeof(ch));
  Serial.write(tail, 4);
'@
        }
        'RawData' {
            return @'
请让单片机/设备按 RawData 协议输出数据，我的串口工具会实时绘制单通道波形：
- 每个原始字节（0~255）直接作为一个采样点，无任何帧头帧尾
- 单通道连续波形，适合 ADC 等高速采样流
- C 示例：Serial.write(adc_byte);
'@
        }
        default {
            return @'
请让单片机/设备按 VOFA+ FireWater 协议输出数据，我的串口工具会实时绘制波形：
- 每行一个采样帧：ch0,ch1,ch2,...（英文逗号分隔，以 \n 结尾）
- 支持负数、小数、科学计数法；单行最多 32 通道
- 普通日志文本行会被自动忽略，不影响解析（行首以 $ 开头的命令行也会跳过）
- C 示例（2 通道）：printf("%.4f,%.4f\n", ch0, ch1);
'@
        }
    }
}

function show_wave_format_dialog {
    $help_text = @'
FireWater（文本协议，默认）
  每行输出各通道数值，逗号分隔，换行(\n)结束一帧：
    printf("%.4f,%.4f\n", ch0, ch1);
  示例行: 1.23,4.56
  支持负数/小数/科学计数法(1.5e-3)，单行最多 32 通道，
  行首 $ 视为命令行自动忽略，含 ANSI 颜色序列的行会先清洗再解析。

JustFloat（二进制协议）
  N 个 float32（小端）+ 固定帧尾 0x00 0x00 0x80 0x7F：
    float ch[3];
    ch[0]=1.2f; ch[1]=3.4f; ch[2]=5.6f;
    HAL_UART_Transmit(&huart,(uint8_t*)ch,sizeof(ch),10);
    uint8_t tail[4]={0x00,0x00,0x80,0x7F};
    HAL_UART_Transmit(&huart,tail,4,10);
  通道数 = 帧长/4，帧间通道数可以变化；帧尾丢失自动重同步。

RawData（原始字节流）
  每个字节(0~255)直接作为一个采样点，单通道连续波形：
    HAL_UART_Transmit(&huart,(uint8_t*)adc_buf,len,10);
  适合 ADC 等高速连续采样流，无帧头帧尾、无通道交织。

-------------------- 提示 --------------------
 1. 波形不动时先核对「协议」下拉框是否与设备输出一致；
    串口有数据但帧数为 0 时状态栏会出现"疑似协议不匹配"提示。
 2. 三种协议都在字节级解析，与接收格式(ASCII/HEX)显示互不影响；
    二进制协议在正文区显示为乱码属正常现象。
 3. 「模拟数据」按钮会按当前协议自动生成测试信号。
'@

    $script:fmt_form = New-Object System.Windows.Forms.Form
    $script:fmt_form.Text = '数据格式说明'
    $script:fmt_form.StartPosition = 'CenterParent'
    $script:fmt_form.Size = New-Object System.Drawing.Size(660, 540)
    $script:fmt_form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $script:fmt_form.MaximizeBox = $false
    $script:fmt_form.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $script:fmt_form.Font = new_font -size 9

    $script:fmt_text = New-Object System.Windows.Forms.TextBox
    $script:fmt_text.Dock = 'Fill'
    $script:fmt_text.Multiline = $true
    $script:fmt_text.ReadOnly = $true
    $script:fmt_text.ScrollBars = 'Vertical'
    $script:fmt_text.WordWrap = $false
    $script:fmt_text.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $script:fmt_text.ForeColor = [System.Drawing.Color]::FromArgb(215, 222, 235)
    $script:fmt_text.Font = new_font -name 'Consolas' -size 9.5
    $script:fmt_text.Text = $help_text

    $fmt_close = New-Object System.Windows.Forms.Button
    $fmt_close.Text = '关闭'
    $fmt_close.Dock = 'Bottom'
    $fmt_close.Height = 34
    style_wave_button -button $fmt_close
    $fmt_close.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $script:fmt_form.CancelButton = $fmt_close

    $script:fmt_form.Controls.Add($script:fmt_text)
    $script:fmt_form.Controls.Add($fmt_close)

    [void]$script:fmt_form.ShowDialog($script:wave_form)
    $script:fmt_form.Dispose()
    $script:fmt_form = $null
    $script:fmt_text = $null
}

function get_wave_presets_path {
    return (Join-Path -Path $script:app_root -ChildPath 'wave_presets.txt')
}

function load_wave_presets {
    param([string]$path = '')
    if ($path -eq '') { $path = get_wave_presets_path }
    $presets = New-Object 'System.Collections.Generic.List[object]'
    try {
        if (Test-Path -LiteralPath $path) {
            $current = $null
            foreach ($line in (Get-Content -LiteralPath $path -Encoding UTF8)) {
                $t = $line.Trim()
                if ($t -eq '') { continue }
                if ($t -match '^\[(.+)\]$') {
                    $current = [pscustomobject]@{ Name = $Matches[1].Trim(); Baud = ''; Protocol = ''; Filter = ''; FilterParam = ''; AutoY = $true; YMin = ''; YMax = ''; Window = '' }
                    [void]$presets.Add($current)
                }
                elseif ($null -ne $current -and $t -match '^([A-Za-z_]+)\s*=\s*(.*)$') {
                    switch ($Matches[1]) {
                        'baud' { $current.Baud = $Matches[2].Trim() }
                        'protocol' { $current.Protocol = $Matches[2].Trim() }
                        'filter' { $current.Filter = $Matches[2].Trim() }
                        'filter_param' { $current.FilterParam = $Matches[2].Trim() }
                        'auto_y' { $current.AutoY = ($Matches[2].Trim() -match '^(1|true|yes)$') }
                        'ymin' { $current.YMin = $Matches[2].Trim() }
                        'ymax' { $current.YMax = $Matches[2].Trim() }
                        'window' { $current.Window = $Matches[2].Trim() }
                    }
                }
            }
        }
    }
    catch {
    }
    return $presets
}

function save_wave_presets {
    param([object[]]$presets, [string]$path = '')
    if ($path -eq '') { $path = get_wave_presets_path }
    try {
        $lines = New-Object 'System.Collections.Generic.List[string]'
        foreach ($p in $presets) {
            [void]$lines.Add(('[{0}]' -f $p.Name))
            [void]$lines.Add(('baud={0}' -f $p.Baud))
            [void]$lines.Add(('protocol={0}' -f $p.Protocol))
            [void]$lines.Add(('filter={0}' -f $p.Filter))
            [void]$lines.Add(('filter_param={0}' -f $p.FilterParam))
            [void]$lines.Add(('auto_y={0}' -f $(if ($p.AutoY) { '1' } else { '0' })))
            [void]$lines.Add(('ymin={0}' -f $p.YMin))
            [void]$lines.Add(('ymax={0}' -f $p.YMax))
            [void]$lines.Add(('window={0}' -f $p.Window))
            [void]$lines.Add('')
        }
        [System.IO.File]::WriteAllLines($path, [string[]]$lines.ToArray(), [System.Text.UTF8Encoding]::new($true))
    }
    catch {
    }
}

function capture_wave_preset {
    # 从当前 UI 抓取一份预设快照（主窗体波特率 + 波形窗口参数）
    $p = [pscustomobject]@{ Name = ''; Baud = ''; Protocol = ''; Filter = ''; FilterParam = ''; AutoY = $true; YMin = ''; YMax = ''; Window = '' }
    if ($combobox_baud) { $p.Baud = $combobox_baud.Text.Trim() }
    if ($script:wcombo_protocol) { $p.Protocol = [string]$script:wcombo_protocol.SelectedItem }
    if ($script:wcombo_filter) { $p.Filter = [string]$script:wcombo_filter.SelectedItem }
    if ($script:wtext_filter_param) { $p.FilterParam = $script:wtext_filter_param.Text.Trim() }
    if ($script:wcheck_autoy) { $p.AutoY = $script:wcheck_autoy.Checked }
    if ($script:wtext_ymin) { $p.YMin = $script:wtext_ymin.Text.Trim() }
    if ($script:wtext_ymax) { $p.YMax = $script:wtext_ymax.Text.Trim() }
    if ($script:wcombo_window) { $p.Window = $script:wcombo_window.Text.Trim() }
    return $p
}

function apply_wave_preset {
    # 逐项写入控件 → 自动走现有处理器（协议切换清缓冲、滤波键失效、Y 手动生效）
    # 波特率只改选择不强制重连；返回 $true 表示找到并应用了预设
    param([string]$name)
    $target = $null
    foreach ($p in @(load_wave_presets)) {
        if ($p.Name -eq $name) { $target = $p; break }
    }
    if ($null -eq $target) { return $false }

    if ($combobox_baud -and $target.Baud -match '^\d+$') {
        $combobox_baud.Text = $target.Baud
    }
    if ($script:wcombo_protocol) {
        $idx = $script:wcombo_protocol.Items.IndexOf($target.Protocol)
        if ($idx -ge 0) { $script:wcombo_protocol.SelectedIndex = $idx }
    }
    if ($script:wcombo_filter) {
        $idx = $script:wcombo_filter.Items.IndexOf($target.Filter)
        if ($idx -ge 0) { $script:wcombo_filter.SelectedIndex = $idx }
    }
    if ($script:wtext_filter_param -and $target.FilterParam -match '^\d+$') {
        $script:wtext_filter_param.Text = $target.FilterParam
    }
    if ($script:wcheck_autoy) {
        $script:wcheck_autoy.Checked = $target.AutoY
    }
    if (-not $target.AutoY) {
        if ($script:wtext_ymin -and $target.YMin -ne '') { $script:wtext_ymin.Text = $target.YMin }
        if ($script:wtext_ymax -and $target.YMax -ne '') { $script:wtext_ymax.Text = $target.YMax }
        apply_wave_y_manual
    }
    if ($script:wcombo_window -and $target.Window -match '^\d+$') {
        $script:wcombo_window.Text = $target.Window
    }
    return $true
}

function refresh_wave_preset_combo {
    param([string]$select = '')
    if (-not $script:wcombo_preset -or $script:wcombo_preset.IsDisposed) { return }
    $script:wcombo_preset.Items.Clear()
    foreach ($p in @(load_wave_presets)) {
        [void]$script:wcombo_preset.Items.Add($p.Name)
    }
    if ($select -ne '') {
        $idx = $script:wcombo_preset.Items.IndexOf($select)
        if ($idx -ge 0) { $script:wcombo_preset.SelectedIndex = $idx }
    }
}

function set_wave_pause_badge {
    # 暂停徽标：常驻占位（文字+底色切换），避免流式布局因 Visible 折叠而跳动
    if ($script:wave_pause_badge -and -not $script:wave_pause_badge.IsDisposed) {
        if ($script:wave_paused) {
            $script:wave_pause_badge.Text = '已暂停'
            $script:wave_pause_badge.BackColor = [System.Drawing.Color]::FromArgb(170, 62, 52)
        }
        else {
            $script:wave_pause_badge.Text = ''
            $script:wave_pause_badge.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
        }
    }
}

function draw_wave_water_level {
    param([System.Drawing.Graphics]$graphics, [int]$width, [int]$height)
    $ratio = 0.0
    if ($script:wave_max_frames -gt 0) {
        $ratio = [Math]::Min(1.0, $script:wave_frame_count / [double]$script:wave_max_frames)
    }
    $graphics.Clear([System.Drawing.Color]::FromArgb(28, 33, 45))
    $track = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(45, 52, 66))
    $graphics.FillRectangle($track, 0, 3, $width, $height - 6)
    $fill_color = [System.Drawing.Color]::FromArgb(90, 130, 170)
    if ($ratio -ge 0.95) { $fill_color = [System.Drawing.Color]::FromArgb(210, 80, 70) }
    elseif ($ratio -ge 0.80) { $fill_color = [System.Drawing.Color]::FromArgb(220, 170, 70) }
    $fill_brush = New-Object System.Drawing.SolidBrush($fill_color)
    $fill_w = [int][Math]::Round(($width - 2) * $ratio)
    if ($fill_w -gt 0) {
        $graphics.FillRectangle($fill_brush, 1, 4, $fill_w, $height - 8)
    }
    $text = ('缓冲 {0:P0}  {1}/{2}' -f $ratio, $script:wave_frame_count, $script:wave_max_frames)
    $font = new_font -size 8
    $text_brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(205, 212, 224))
    $graphics.DrawString($text, $font, $text_brush, 3, 2)
    $text_brush.Dispose()
    $font.Dispose()
    $fill_brush.Dispose()
    $track.Dispose()
}

function open_wave_window {
    if ($script:wave_form -and -not $script:wave_form.IsDisposed) {
        $script:wave_form.Activate()
        return
    }

    $script:wave_channel_checks = New-Object 'System.Collections.Generic.List[object]'
    $script:wave_paused = $false
    $script:wave_last_painted_frame = 0
    $script:wave_fps_count = 0
    $script:wave_fps_value = 0
    $script:wave_fps_reset_at = [datetime]::MinValue
    $script:wave_hint_last_rx = $script:rx_bytes_total
    $script:wave_hint_last_frames = $script:wave_frame_total

    $script:wave_form = New-Object System.Windows.Forms.Form
    $script:wave_form.Text = 'FireWater 实时波形 - RK3562 串口收发工具 V6'
    $script:wave_form.StartPosition = 'CenterParent'
    $script:wave_form.Size = New-Object System.Drawing.Size(1260, 720)
    $script:wave_form.MinimumSize = New-Object System.Drawing.Size(1000, 500)
    $script:wave_form.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $script:wave_form.Font = new_font -name 'Microsoft YaHei UI' -size 9
    $script:wave_form.KeyPreview = $true

    $wave_toolbar = New-Object System.Windows.Forms.Panel
    $wave_toolbar.Dock = 'Top'
    $wave_toolbar.Height = 76
    $wave_toolbar.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $script:wbutton_sim = New-Object System.Windows.Forms.Button
    $script:wbutton_sim.Text = '▶ 模拟数据'
    $script:wbutton_sim.Location = New-Object System.Drawing.Point(8, 8)
    $script:wbutton_sim.Size = New-Object System.Drawing.Size(88, 26)

    $script:wbutton_pause = New-Object System.Windows.Forms.Button
    $script:wbutton_pause.Text = '⏸ 暂停'
    $script:wbutton_pause.Location = New-Object System.Drawing.Point(104, 8)
    $script:wbutton_pause.Size = New-Object System.Drawing.Size(64, 26)

    $script:wbutton_clear = New-Object System.Windows.Forms.Button
    $script:wbutton_clear.Text = '清空'
    $script:wbutton_clear.Location = New-Object System.Drawing.Point(176, 8)
    $script:wbutton_clear.Size = New-Object System.Drawing.Size(64, 26)

    $script:wbutton_csv = New-Object System.Windows.Forms.Button
    $script:wbutton_csv.Text = '↓ 导出'
    $script:wbutton_csv.Location = New-Object System.Drawing.Point(248, 8)
    $script:wbutton_csv.Size = New-Object System.Drawing.Size(80, 26)

    $script:wbutton_allch = New-Object System.Windows.Forms.Button
    $script:wbutton_allch.Text = '全不选'
    $script:wbutton_allch.Location = New-Object System.Drawing.Point(336, 8)
    $script:wbutton_allch.Size = New-Object System.Drawing.Size(76, 26)

    $script:wcheck_autoy = New-Object DarkCheckBox
    $script:wcheck_autoy.Text = '自动Y轴'
    $script:wcheck_autoy.Location = New-Object System.Drawing.Point(424, 12)
    $script:wcheck_autoy.AutoSize = $true
    $script:wcheck_autoy.Checked = $true
    $script:wcheck_autoy.ForeColor = [System.Drawing.Color]::FromArgb(205, 212, 224)

    $script:wbutton_auto = New-Object System.Windows.Forms.Button
    $script:wbutton_auto.Text = '↻ 自动范围'
    $script:wbutton_auto.Location = New-Object System.Drawing.Point(515, 8)
    $script:wbutton_auto.Size = New-Object System.Drawing.Size(96, 26)

    $wlabel_ymin = New-Object System.Windows.Forms.Label
    $wlabel_ymin.Text = 'Ymin'
    $wlabel_ymin.Location = New-Object System.Drawing.Point(612, 14)
    $wlabel_ymin.AutoSize = $true
    $wlabel_ymin.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wtext_ymin = New-Object System.Windows.Forms.TextBox
    $script:wtext_ymin.Location = New-Object System.Drawing.Point(648, 9)
    $script:wtext_ymin.Size = New-Object System.Drawing.Size(64, 24)
    $script:wtext_ymin.Text = '-1'

    $wlabel_ymax = New-Object System.Windows.Forms.Label
    $wlabel_ymax.Text = 'Ymax'
    $wlabel_ymax.Location = New-Object System.Drawing.Point(722, 14)
    $wlabel_ymax.AutoSize = $true
    $wlabel_ymax.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wtext_ymax = New-Object System.Windows.Forms.TextBox
    $script:wtext_ymax.Location = New-Object System.Drawing.Point(758, 9)
    $script:wtext_ymax.Size = New-Object System.Drawing.Size(64, 24)
    $script:wtext_ymax.Text = '1'

    $wlabel_window = New-Object System.Windows.Forms.Label
    $wlabel_window.Text = '窗口'
    $wlabel_window.Location = New-Object System.Drawing.Point(834, 14)
    $wlabel_window.AutoSize = $true
    $wlabel_window.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wcombo_window = New-Object DarkComboBox
    $script:wcombo_window.Location = New-Object System.Drawing.Point(870, 9)
    $script:wcombo_window.Size = New-Object System.Drawing.Size(84, 24)
    $script:wcombo_window.DropDownStyle = 'DropDown'
    [void]$script:wcombo_window.Items.AddRange(@('256', '500', '1000', '2000', '5000', '10000'))
    $script:wcombo_window.Text = '1000'

    $wlabel_protocol = New-Object System.Windows.Forms.Label
    $wlabel_protocol.Text = '协议'
    $wlabel_protocol.Location = New-Object System.Drawing.Point(964, 14)
    $wlabel_protocol.AutoSize = $true
    $wlabel_protocol.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wcombo_protocol = New-Object DarkComboBox
    $script:wcombo_protocol.Location = New-Object System.Drawing.Point(1000, 9)
    $script:wcombo_protocol.Size = New-Object System.Drawing.Size(130, 24)
    $script:wcombo_protocol.DropDownStyle = 'DropDownList'
    [void]$script:wcombo_protocol.Items.AddRange(@('FireWater', 'JustFloat', 'RawData'))
    $script:wcombo_protocol.SelectedItem = $script:wave_protocol

    $script:wbutton_copyfmt = New-Object System.Windows.Forms.Button
    $script:wbutton_copyfmt.Text = '复制AI格式'
    $script:wbutton_copyfmt.Location = New-Object System.Drawing.Point(1140, 8)
    $script:wbutton_copyfmt.Size = New-Object System.Drawing.Size(110, 26)

    # ---- 工具栏第二行：滤波 + 插件 ----
    $wlabel_filter = New-Object System.Windows.Forms.Label
    $wlabel_filter.Text = '滤波'
    $wlabel_filter.Location = New-Object System.Drawing.Point(10, 48)
    $wlabel_filter.AutoSize = $true
    $wlabel_filter.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wcombo_filter = New-Object DarkComboBox
    $script:wcombo_filter.Location = New-Object System.Drawing.Point(50, 44)
    $script:wcombo_filter.Size = New-Object System.Drawing.Size(130, 24)
    $script:wcombo_filter.DropDownStyle = 'DropDownList'
    [void]$script:wcombo_filter.Items.AddRange(@('关', '滑动平均', '零相位低通'))
    $script:wcombo_filter.SelectedItem = '关'

    $wlabel_filter_param = New-Object System.Windows.Forms.Label
    $wlabel_filter_param.Text = '强度'
    $wlabel_filter_param.Location = New-Object System.Drawing.Point(190, 48)
    $wlabel_filter_param.AutoSize = $true
    $wlabel_filter_param.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wtext_filter_param = New-Object System.Windows.Forms.TextBox
    $script:wtext_filter_param.Location = New-Object System.Drawing.Point(228, 44)
    $script:wtext_filter_param.Size = New-Object System.Drawing.Size(56, 24)
    $script:wtext_filter_param.Text = '16'


    # ---- 场景预设（第二行中部）：波特率+协议+滤波+视图 一键切换 ----
    $wlabel_preset = New-Object System.Windows.Forms.Label
    $wlabel_preset.Text = '预设'
    $wlabel_preset.Location = New-Object System.Drawing.Point(300, 48)
    $wlabel_preset.AutoSize = $true
    $wlabel_preset.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)

    $script:wcombo_preset = New-Object DarkComboBox
    $script:wcombo_preset.Location = New-Object System.Drawing.Point(338, 44)
    $script:wcombo_preset.Size = New-Object System.Drawing.Size(140, 24)
    $script:wcombo_preset.DropDownStyle = 'DropDownList'

    $script:wbutton_preset_save = New-Object System.Windows.Forms.Button
    $script:wbutton_preset_save.Text = '存预设'
    $script:wbutton_preset_save.Location = New-Object System.Drawing.Point(484, 42)
    $script:wbutton_preset_save.Size = New-Object System.Drawing.Size(64, 24)

    $script:wbutton_preset_del = New-Object System.Windows.Forms.Button
    $script:wbutton_preset_del.Text = '删'
    $script:wbutton_preset_del.Location = New-Object System.Drawing.Point(552, 42)
    $script:wbutton_preset_del.Size = New-Object System.Drawing.Size(44, 24)

    foreach ($toolbar_button in @($script:wbutton_sim, $script:wbutton_pause, $script:wbutton_clear, $script:wbutton_csv, $script:wbutton_allch, $script:wbutton_copyfmt, $script:wbutton_preset_save, $script:wbutton_preset_del)) {
        style_wave_button -button $toolbar_button
    }
    # 主操作用强调色实底（悬停变亮/按下变暗）
    style_wave_button -button $script:wbutton_auto -accent

    $wave_toolbar.Controls.AddRange(@(
        $script:wbutton_sim,
        $script:wbutton_pause,
        $script:wbutton_clear,
        $script:wbutton_csv,
        $script:wbutton_allch,
        $script:wcheck_autoy,
        $script:wbutton_auto,
        $wlabel_ymin,
        $script:wtext_ymin,
        $wlabel_ymax,
        $script:wtext_ymax,
        $wlabel_window,
        $script:wcombo_window,
        $wlabel_protocol,
        $script:wcombo_protocol,
        $script:wbutton_copyfmt,
        $wlabel_filter,
        $script:wcombo_filter,
        $wlabel_filter_param,
        $script:wtext_filter_param,
        $wlabel_preset,
        $script:wcombo_preset,
        $script:wbutton_preset_save,
        $script:wbutton_preset_del
    ))

    # 分组分隔线：数据源 | 通道 | 视图 | 时间窗 | 协议（第二行：滤波 | 预设）
    $sep_color = [System.Drawing.Color]::FromArgb(58, 66, 84)
    foreach ($sep_pos in @(@(332, 8, 26), @(417, 8, 26), @(605, 8, 26), @(827, 8, 26), @(959, 8, 26), @(292, 44, 24))) {
        $sep = New-Object System.Windows.Forms.Label
        $sep.Location = New-Object System.Drawing.Point($sep_pos[0], $sep_pos[1])
        $sep.Size = New-Object System.Drawing.Size(2, $sep_pos[2])
        $sep.BackColor = $sep_color
        [void]$wave_toolbar.Controls.Add($sep)
    }

    # ---- 波形窗口输入控件深色化 ----
    # 工具栏底色 #1C212D，但这些输入控件（组合框/复选框/数字框）此前完全没上过深色，
    # 渲染成系统浅色（浅底深字 + 白箭头），和深色波形面板割裂。这里一次性铺深色，
    # 组合框/复选框再开自绘（UseDark）。波形窗口固定深色，不随主窗体主题切换。
    $wave_input_bg = [System.Drawing.Color]::FromArgb(38, 45, 58)    # #262D3A
    $wave_text_fg = [System.Drawing.Color]::FromArgb(225, 230, 240)  # #E1E6F0
    $wave_dim_fg = [System.Drawing.Color]::FromArgb(150, 158, 172)   # #969EAC
    foreach ($wc in @($script:wcombo_window, $script:wcombo_protocol, $script:wcombo_filter, $script:wcombo_preset)) {
        $wc.BackColor = $wave_input_bg
        $wc.ForeColor = $wave_text_fg
        $wc.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $wc.UseDark = $true
        $wc.ArrowBack = $wave_input_bg
        $wc.ArrowFore = $wave_text_fg
        $wc.BorderColor = $sep_color
        # 下拉弹层衬底/子 EDIT 画刷颜色对齐输入框（DarkComboBox 内部惰性建刷用这两个色）
        $wc.EnableDarkEdit($true, $wave_input_bg, $wave_text_fg)
    }
    foreach ($wt in @($script:wtext_ymin, $script:wtext_ymax, $script:wtext_filter_param)) {
        $wt.BackColor = $wave_input_bg
        $wt.ForeColor = $wave_text_fg
    }
    $script:wcheck_autoy.BackColor = $wave_toolbar.BackColor
    $script:wcheck_autoy.ForeColor = $wave_text_fg
    $script:wcheck_autoy.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $script:wcheck_autoy.UseDark = $true
    $script:wcheck_autoy.BoxBorder = $wave_dim_fg
    $script:wcheck_autoy.MarkColor = $wave_text_fg

    # ---- 全按钮 ToolTip（此前只有插件按钮有悬浮提示）----
    $wave_tooltip = New-Object System.Windows.Forms.ToolTip
    $wave_tooltip.SetToolTip($script:wbutton_sim, '用当前协议生成模拟信号源（无设备时验证波形/频谱链路）')
    $wave_tooltip.SetToolTip($script:wbutton_pause, '暂停/继续：缓冲真冻结，RX/日志不受影响（快捷键 空格）')
    $wave_tooltip.SetToolTip($script:wbutton_clear, '清空波形缓冲（不清串口记录）')
    $wave_tooltip.SetToolTip($script:wbutton_csv, '导出 PNG 视图 / CSV / TXT（快捷键 Ctrl+E）')
    $wave_tooltip.SetToolTip($script:wbutton_allch, '通道全选 / 全不选')
    $wave_tooltip.SetToolTip($script:wcheck_autoy, '自动 Y 轴：跟随数据范围；关闭后用 Ymin/Ymax')
    $wave_tooltip.SetToolTip($script:wbutton_auto, '复位视图：自动范围 + 跟随最新 + 窗口 1000（双击曲线区同效）')
    $wave_tooltip.SetToolTip($script:wcombo_window, '显示最近 N 帧滚动窗口')
    $wave_tooltip.SetToolTip($script:wcombo_protocol, '帧解析协议，切换会清空缓冲')
    $wave_tooltip.SetToolTip($script:wbutton_copyfmt, '复制当前协议的 AI 格式说明到剪贴板')
    $wave_tooltip.SetToolTip($script:wcombo_filter, '非破坏滤波：切换/改参数对全部旧数据即时生效')
    $wave_tooltip.SetToolTip($script:wtext_filter_param, '滑动平均=窗口点数；零相位低通=强度（越小截止越低）')
    $wave_tooltip.SetToolTip($script:wcombo_preset, '场景预设：一键切换 波特率/协议/滤波/视图')
    $wave_tooltip.SetToolTip($script:wbutton_preset_save, '把当前 波特率/协议/滤波/视图 存为命名预设')
    $wave_tooltip.SetToolTip($script:wbutton_preset_del, '删除选中的预设')

    $script:wave_channel_flow = New-Object System.Windows.Forms.FlowLayoutPanel
    $script:wave_channel_flow.Dock = 'Right'
    $script:wave_channel_flow.Width = 150
    $script:wave_channel_flow.FlowDirection = 'TopDown'
    $script:wave_channel_flow.WrapContents = $false
    $script:wave_channel_flow.AutoScroll = $true
    $script:wave_channel_flow.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $script:wave_channel_flow.Padding = New-Object System.Windows.Forms.Padding(8, 6, 8, 6)

    $script:wave_channel_header = New-Object System.Windows.Forms.Label
    $script:wave_channel_header.Text = '通道（色块显隐 ✎改名）'
    $script:wave_channel_header.AutoSize = $true
    $script:wave_channel_header.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    [void]$script:wave_channel_flow.Controls.Add($script:wave_channel_header)

    # 面板宽度联动（拖分隔条/窗口缩放）：通道行与 XY 控件区宽度统一、✎ 右对齐
    $script:wave_channel_flow.Add_Resize({
        sync_wave_panel_row_widths
    })

    # 通道列表与曲线区之间的可拖分隔条（90~320px；用屏幕坐标算增量，鼠标快移不丢）
    $script:wave_splitter = New-Object System.Windows.Forms.Panel
    $script:wave_splitter.Dock = 'Right'
    $script:wave_splitter.Width = 6
    $script:wave_splitter.BackColor = [System.Drawing.Color]::FromArgb(45, 52, 66)
    $script:wave_splitter.Cursor = [System.Windows.Forms.Cursors]::VSplit
    $script:wave_splitter.Add_MouseDown({
        param($sender, $event_args)
        if ($event_args.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
            $script:wave_splitter_drag = @{
                StartScreenX = [System.Windows.Forms.Cursor]::Position.X
                StartWidth = $script:wave_channel_flow.Width
            }
        }
    })
    $script:wave_splitter.Add_MouseMove({
        param($sender, $event_args)
        if ($script:wave_splitter_drag) {
            $drag = $script:wave_splitter_drag
            $new_width = $drag.StartWidth - ([System.Windows.Forms.Cursor]::Position.X - $drag.StartScreenX)
            if ($new_width -lt 90) { $new_width = 90 }
            if ($new_width -gt 320) { $new_width = 320 }
            if ($new_width -ne $script:wave_channel_flow.Width) {
                $script:wave_channel_flow.Width = $new_width
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        }
    })
    $script:wave_splitter.Add_MouseUp({
        $script:wave_splitter_drag = $null
    })
    $script:wave_splitter.Add_MouseLeave({
        $script:wave_splitter_drag = $null
    })

    $script:wave_panel = New-Object System.Windows.Forms.Panel
    $script:wave_panel.Dock = 'Fill'
    $script:wave_panel.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $double_buffer_property = $script:wave_panel.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'Instance,NonPublic')
    if ($double_buffer_property) {
        $double_buffer_property.SetValue($script:wave_panel, $true, $null)
    }

    $script:wave_panel.Add_Paint({
        param($sender, $event_args)
        try {
            if ($script:wave_xy_on) {
                draw_wave_xy_graphics -graphics $event_args.Graphics -width $script:wave_panel.ClientSize.Width -height $script:wave_panel.ClientSize.Height
            }
            else {
                draw_wave_graphics -graphics $event_args.Graphics -width $script:wave_panel.ClientSize.Width -height $script:wave_panel.ClientSize.Height
            }
        }
        catch {
            write_runtime_log -message ('波形绘制异常: {0}' -f $_.Exception.Message)
        }
    })

    $script:wave_panel.Add_Resize({
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            $script:wave_panel.Invalidate()
        }
    })

    # 滚轮缩放：纵轴区/曲线区缩 Y 范围（以鼠标处数值为锚点），横轴区调显示窗口帧数
    $script:wave_panel.TabStop = $false
    $script:wave_panel.Add_MouseEnter({
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            [void]$script:wave_panel.Focus()
        }
    })

    $script:wave_panel.Add_MouseWheel({
        param($sender, $event_args)
        try {
            # XY 模式：纵轴缩放/窗口调整对相位图无意义，不改动共享的视图状态
            if ($script:wave_xy_on) {
                return
            }
            $rect = get_wave_plot_rect
            if ($rect.Width -lt 20 -or $rect.Height -lt 20) {
                return
            }

            $zoom_in = ($event_args.Delta -gt 0)

            if ($event_args.Y -gt ($rect.Top + $rect.Height)) {
                # 横轴区：滚轮调整显示窗口帧数（历史浏览时保持窗口中心）
                $factor = 0.8
                if (-not $zoom_in) {
                    $factor = 1.25
                }
                $new_window = [int][Math]::Round($script:wave_window_frames * $factor)
                if ($new_window -ne [int]$script:wave_window_frames) {
                    set_wave_view_width -new_width $new_window -total $script:wave_frame_count
                    $script:wave_panel.Invalidate()
                }
                return
            }

            # 纵轴区或曲线区：缩放 Y 范围
            $checks = $script:wave_channel_checks
            $wheel_enabled = New-Object bool[] $script:wave_ch_values.Count
            for ($c = 0; $c -lt $script:wave_ch_values.Count; $c++) {
                if ($checks.Count -le $c -or $checks[$c].Checked) {
                    $wheel_enabled[$c] = $true
                }
            }
            $yr = get_wave_y_range -enabled $wheel_enabled -total $script:wave_frame_count -channel_count $script:wave_ch_values.Count
            $anchor = $yr.Max - (($event_args.Y - $rect.Top) / $rect.Height) * ($yr.Max - $yr.Min)
            $scale_factor = 1.25
            if ($zoom_in) {
                $scale_factor = 0.8
            }
            $new_min = $anchor - ($anchor - $yr.Min) * $scale_factor
            $new_max = $anchor + ($yr.Max - $anchor) * $scale_factor
            if (($new_max - $new_min) -lt 0.000000000001) {
                return
            }
            if ($script:wave_auto_y) {
                $script:wcheck_autoy.Checked = $false
            }
            $script:wave_y_min = $new_min
            $script:wave_y_max = $new_max
            $script:wave_panel.Invalidate()
        }
        catch {
            write_runtime_log -message ('波形滚轮缩放异常: {0}' -f $_.Exception.Message)
        }
    })

    # 鼠标拖动：横轴区/曲线区水平拖 = 左右平移查看数据；纵轴区/曲线区垂直拖 = 上下平移 Y（手动 Y 轴时）
    $script:wave_panel.Add_MouseDown({
        param($sender, $event_args)
        if ($event_args.Button -ne [System.Windows.Forms.MouseButtons]::Left) {
            return
        }

        $rect = get_wave_plot_rect
        if ($rect.Width -lt 20 -or $rect.Height -lt 20) {
            return
        }

        $zone = 'both'
        if ($event_args.Y -gt ($rect.Top + $rect.Height)) {
            $zone = 'h'
        }
        elseif ($event_args.X -lt $rect.Left) {
            $zone = 'v'
        }

        $script:wave_drag_state = @{
            Zone = $zone
            LastX = $event_args.X
            LastY = $event_args.Y
            MinY = [double]$script:wave_y_min
            MaxY = [double]$script:wave_y_max
            AutoY = [bool]$script:wave_auto_y
        }
    })

    $script:wave_panel.Add_MouseMove({
        param($sender, $event_args)

        # 十字光标：曲线区内跟随鼠标，区外清除
        $rect_now = get_wave_plot_rect
        $in_plot_now = ($event_args.X -ge $rect_now.Left -and $event_args.X -le ($rect_now.Left + $rect_now.Width) -and
            $event_args.Y -ge $rect_now.Top -and $event_args.Y -le ($rect_now.Top + $rect_now.Height))
        $crosshair_changed = $false
        if ($in_plot_now) {
            if (-not $script:wave_crosshair -or $script:wave_crosshair.Px -ne $event_args.X -or $script:wave_crosshair.Py -ne $event_args.Y) {
                $script:wave_crosshair = @{ Px = [double]$event_args.X; Py = [double]$event_args.Y }
                $crosshair_changed = $true
            }
        }
        elseif ($script:wave_crosshair) {
            $script:wave_crosshair = $null
            $crosshair_changed = $true
        }

        if (-not $script:wave_drag_state) {
            if ($crosshair_changed -and $script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
            return
        }

        $drag = $script:wave_drag_state
        $dx = $event_args.X - $drag.LastX
        $dy = $event_args.Y - $drag.LastY
        if ($dx -eq 0 -and $dy -eq 0) {
            return
        }
        $drag.LastX = $event_args.X
        $drag.LastY = $event_args.Y

        $rect = get_wave_plot_rect
        $changed = $false

        if (($drag.Zone -eq 'h' -or $drag.Zone -eq 'both') -and $dx -ne 0 -and $rect.Width -gt 0) {
            $view = get_wave_view_range -total $script:wave_frame_count
            $view_width = [Math]::Max(1, ($view.End - $view.Start))
            $delta_frames = [int][Math]::Round(-$dx * $view_width / $rect.Width)
            if ($delta_frames -ne 0) {
                set_wave_view_pan -delta_frames $delta_frames -total $script:wave_frame_count
                $changed = $true
            }
        }

        if (($drag.Zone -eq 'v' -or $drag.Zone -eq 'both') -and $dy -ne 0 -and $rect.Height -gt 0 -and -not $drag.AutoY) {
            $y_range = $drag.MaxY - $drag.MinY
            if ($y_range -gt 0) {
                # 内容跟随手：往下拖（dy>0）= 数据下移 = 值域上移
                $shift = $dy * $y_range / $rect.Height
                $script:wave_y_min = $drag.MinY + $shift
                $script:wave_y_max = $drag.MaxY + $shift
                $changed = $true
            }
        }

        if ($changed -or $crosshair_changed) {
            $script:wave_panel.Invalidate()
        }
    })

    $script:wave_panel.Add_MouseUp({
        param($sender, $event_args)
        $script:wave_drag_state = $null
    })

    $script:wave_panel.Add_MouseLeave({
        param($sender, $event_args)
        if ($script:wave_crosshair) {
            $script:wave_crosshair = $null
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })

    # 双击曲线区 = 复位视图（与「自动范围」按钮同效；XY 模式下没有时域视图可复位）
    $script:wave_panel.Add_DoubleClick({
        if ($script:wave_xy_on) {
            return
        }
        if ($script:wbutton_auto -and -not $script:wbutton_auto.IsDisposed) {
            $script:wbutton_auto.PerformClick()
        }
    })

    # ---- 曲线区右键菜单：常用操作不再依赖工具栏位置 ----
    $wave_menu = New-Object System.Windows.Forms.ContextMenuStrip
    $mi_pause = $wave_menu.Items.Add('暂停')
    $mi_pause.Add_Click({
        if ($script:wbutton_pause -and -not $script:wbutton_pause.IsDisposed) {
            $script:wbutton_pause.PerformClick()
        }
    })
    $mi_reset = $wave_menu.Items.Add('自动范围（复位视图）')
    $mi_reset.Add_Click({
        if ($script:wbutton_auto -and -not $script:wbutton_auto.IsDisposed) {
            $script:wbutton_auto.PerformClick()
        }
    })
    $mi_export = $wave_menu.Items.Add('导出…')
    $mi_export.Add_Click({
        show_wave_export_dialog
    })
    $mi_record = $wave_menu.Items.Add('开始录制')
    $mi_record.Add_Click({
        if ($script:wbutton_record -and -not $script:wbutton_record.IsDisposed) {
            $script:wbutton_record.PerformClick()
        }
    })
    $mi_clear = $wave_menu.Items.Add('清空缓冲')
    $mi_clear.Add_Click({
        reset_wave_buffers
    })
    $mi_format = $wave_menu.Items.Add('格式说明')
    $mi_format.Add_Click({
        show_wave_format_dialog
    })
    $wave_menu.Add_Opening({
        $mi_pause.Text = $(if ($script:wave_paused) { '继续' } else { '暂停' })
        $mi_record.Text = $(if ($script:session_record_writer) { '停止录制' } else { '开始录制' })
    })
    $script:wave_panel.ContextMenuStrip = $wave_menu

    # 三点范围滑块（VOFA+ 式）：左红点 = 窗口起点，绿点 = 中心（拖动平移），右紫点 = 窗口终点；
    # 拖两侧点改范围时绿点自动保持在两点正中
    $script:wave_slider_panel = New-Object System.Windows.Forms.Panel
    $script:wave_slider_panel.Dock = 'Bottom'
    $script:wave_slider_panel.Height = 34
    $script:wave_slider_panel.BackColor = [System.Drawing.Color]::FromArgb(24, 28, 38)
    $script:wave_slider_panel.Add_Paint({
        param($sender, $event_args)
        try {
            draw_wave_slider -graphics $event_args.Graphics -width $sender.ClientSize.Width -height $sender.ClientSize.Height
        }
        catch {
        }
    })
    $script:wave_slider_panel.Add_MouseDown({
        param($sender, $event_args)
        if ($event_args.Button -ne [System.Windows.Forms.MouseButtons]::Left) {
            return
        }
        $total = $script:wave_frame_count
        if ($total -lt 2) {
            return
        }
        $hit = get_wave_slider_hit -x $event_args.X -width $sender.ClientSize.Width -total $total
        if ($hit -eq 'none') {
            return
        }

        # 绝对同步：记录"点当前位置 - 鼠标当前位置"的帧偏移，移动时鼠标指哪点在哪
        $metrics = get_wave_slider_metrics -width $sender.ClientSize.Width
        $view = get_wave_view_range -total $total
        $mouse_frame = ($event_args.X - $metrics.Left) / $metrics.Span * ($total - 1)
        $point_frame = switch ($hit) {
            'start' { [double]$view.Start }
            'end' { [double]($view.End - 1) }
            default { [double](($view.Start + $view.End - 1) / 2.0) }
        }
        $script:wave_slider_drag = @{
            Point = $hit
            GrabOffset = ($point_frame - $mouse_frame)
            Width = ($view.End - $view.Start)
            AnchorFrame = $(if ($hit -eq 'start') { [double]$view.End } else { [double]$view.Start })
            LastX = $event_args.X
        }
        $sender.Cursor = [System.Windows.Forms.Cursors]::Hand
    })
    $script:wave_slider_panel.Add_MouseMove({
        param($sender, $event_args)
        $total = $script:wave_frame_count
        if ($total -lt 2) {
            return
        }

        if (-not $script:wave_slider_drag) {
            # 无拖动：更新悬停高亮与光标
            $hit = get_wave_slider_hit -x $event_args.X -width $sender.ClientSize.Width -total $total
            if ($hit -ne $script:wave_slider_hover) {
                $script:wave_slider_hover = $hit
                $sender.Cursor = $(if ($hit -ne 'none') { [System.Windows.Forms.Cursors]::Hand } else { [System.Windows.Forms.Cursors]::Default })
                if (-not $sender.IsDisposed) {
                    $sender.Invalidate()
                }
            }
            return
        }

        $drag = $script:wave_slider_drag
        $metrics = get_wave_slider_metrics -width $sender.ClientSize.Width
        $mouse_frame = ($event_args.X - $metrics.Left) / $metrics.Span * ($total - 1)

        switch ($drag.Point) {
            'mid' {
                # 拖中点：窗口中心直接落在鼠标位置（保持宽度）
                $target = $mouse_frame + $drag.GrabOffset
                $half = $drag.Width / 2.0
                set_wave_view_window -new_start ([int][Math]::Round($target - $half)) -new_end ([int][Math]::Round($target + $half)) -total $total -anchor 'mid'
            }
            'start' {
                # 拖左点：起点跟随鼠标，终点钉在按下时的位置，中心自动重算
                set_wave_view_window -new_start ([int][Math]::Round($mouse_frame + $drag.GrabOffset)) -new_end ([int]$drag.AnchorFrame) -total $total -anchor 'start'
            }
            'end' {
                # 拖右点：终点跟随鼠标，起点钉在按下时的位置，中心自动重算
                set_wave_view_window -new_start ([int]$drag.AnchorFrame) -new_end ([int][Math]::Round($mouse_frame + $drag.GrabOffset) + 1) -total $total -anchor 'end'
            }
        }

        if (-not $sender.IsDisposed) {
            $sender.Invalidate()
        }
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            $script:wave_panel.Invalidate()
        }
    })
    $script:wave_slider_panel.Add_MouseUp({
        param($sender, $event_args)
        $script:wave_slider_drag = $null
    })
    $script:wave_slider_panel.Add_MouseLeave({
        param($sender, $event_args)
        $script:wave_slider_hover = 'none'
        if (-not $sender.IsDisposed) {
            $sender.Cursor = [System.Windows.Forms.Cursors]::Default
            $sender.Invalidate()
        }
    })

    $wave_statusbar = New-Object System.Windows.Forms.Panel
    $wave_statusbar.Dock = 'Bottom'
    $wave_statusbar.Height = 26
    $wave_statusbar.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    $script:wlabel_stats = New-Object System.Windows.Forms.Label
    $script:wlabel_stats.Text = '帧 0 | 0 帧/s | 忽略 0'
    $script:wlabel_stats.Location = New-Object System.Drawing.Point(8, 5)
    $script:wlabel_stats.AutoSize = $true
    $script:wlabel_stats.ForeColor = [System.Drawing.Color]::FromArgb(168, 178, 195)
    $wave_statusbar.Controls.Add($script:wlabel_stats)
    $wave_tooltip.SetToolTip($script:wlabel_stats, '空格=暂停/继续 | Ctrl+E=导出 | 双击曲线区=复位视图')

    # 右侧组（徽标 + 水位 + 录制/格式说明）：用 FlowLayoutPanel 免锚定——
    # 状态栏初始宽度 200 时 Right 锚按错误偏移计算，窗口变宽后按钮被推出屏幕外（实测踩过）
    $wave_status_right = New-Object System.Windows.Forms.FlowLayoutPanel
    $wave_status_right.Dock = 'Right'
    $wave_status_right.Width = 470
    $wave_status_right.FlowDirection = 'LeftToRight'
    $wave_status_right.WrapContents = $false
    $wave_status_right.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)

    # 暂停徽标：常驻占位避免暂停切换时右侧组件跳动，非暂停时融入背景
    $script:wave_pause_badge = New-Object System.Windows.Forms.Label
    $script:wave_pause_badge.Text = ''
    $script:wave_pause_badge.Size = New-Object System.Drawing.Size(64, 18)
    $script:wave_pause_badge.Margin = New-Object System.Windows.Forms.Padding(0, 4, 8, 0)
    $script:wave_pause_badge.TextAlign = 'MiddleCenter'
    $script:wave_pause_badge.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $script:wave_pause_badge.ForeColor = [System.Drawing.Color]::White
    $wave_status_right.Controls.Add($script:wave_pause_badge)

    # 缓冲水位条：>80% 黄 / ≥95% 红（满 20 万帧自动丢弃最旧）
    $script:wave_water_panel = New-Object System.Windows.Forms.Panel
    $script:wave_water_panel.Size = New-Object System.Drawing.Size(190, 18)
    $script:wave_water_panel.Margin = New-Object System.Windows.Forms.Padding(0, 4, 10, 0)
    $script:wave_water_panel.Add_Paint({
        param($sender, $event_args)
        try {
            draw_wave_water_level -graphics $event_args.Graphics -width $sender.ClientSize.Width -height $sender.ClientSize.Height
        }
        catch {
        }
    })
    $wave_status_right.Controls.Add($script:wave_water_panel)
    $wave_tooltip.SetToolTip($script:wave_water_panel, '缓冲水位：满后自动丢弃最旧数据（不影响 RX/日志）')

    $script:wbutton_format = New-Object System.Windows.Forms.Button
    $script:wbutton_format.Text = '格式说明'
    $script:wbutton_format.Size = New-Object System.Drawing.Size(88, 22)
    $script:wbutton_format.Margin = New-Object System.Windows.Forms.Padding(0, 2, 6, 0)
    style_wave_button -button $script:wbutton_format
    $wave_status_right.Controls.Add($script:wbutton_format)

    $script:wbutton_record = New-Object System.Windows.Forms.Button
    $script:wbutton_record.Text = '录制'
    $script:wbutton_record.Size = New-Object System.Drawing.Size(88, 22)
    $script:wbutton_record.Margin = New-Object System.Windows.Forms.Padding(0, 2, 0, 0)
    style_wave_button -button $script:wbutton_record
    $wave_status_right.Controls.Add($script:wbutton_record)

    $wave_statusbar.Controls.Add($wave_status_right)

    # 页签化（插件合同 v2）：波形与所有插件共用这一个窗口——第 0 页是波形本体，
    # 每个已注册插件一页（load_wave_plugins 追加），首次切入才构建内容。
    # 原本直接挂在窗体上的六个控件整体移入「波形」页，Dock 语义在 TabPage 内不变。
    $script:wave_tabs = New-Object System.Windows.Forms.TabControl
    $script:wave_tabs.Dock = 'Fill'
    $script:wave_tabs.Font = new_font -name 'Microsoft YaHei UI' -size 9
    # 深色页签：自绘（窗口固定深色，原生页签条是亮的）
    $script:wave_tabs.DrawMode = 'OwnerDrawFixed'
    $script:wave_tabs.ItemSize = New-Object System.Drawing.Size(0, 26)
    $script:wave_tabs.Add_DrawItem({
        param($sender, $event_args)
        $tab_bg = [System.Drawing.Color]::FromArgb(28, 33, 45)
        $tab_fg = [System.Drawing.Color]::FromArgb(168, 178, 195)
        if (($event_args.State -band [System.Windows.Forms.DrawItemState]::Selected) -ne 0) {
            $tab_bg = [System.Drawing.Color]::FromArgb(20, 24, 33)
            $tab_fg = [System.Drawing.Color]::FromArgb(225, 230, 240)
        }
        $brush = New-Object System.Drawing.SolidBrush($tab_bg)
        $event_args.Graphics.FillRectangle($brush, $event_args.Bounds)
        $brush.Dispose()
        $text_brush = New-Object System.Drawing.SolidBrush($tab_fg)
        $sf = New-Object System.Drawing.StringFormat
        $sf.Alignment = 'Center'
        $sf.LineAlignment = 'Center'
        $event_args.Graphics.DrawString($sender.TabPages[$event_args.Index].Text, $sender.Font, $text_brush, [System.Drawing.RectangleF]::op_Implicit($event_args.Bounds), $sf)
        $text_brush.Dispose()
        $sf.Dispose()
    })

    $wave_home = New-Object System.Windows.Forms.TabPage
    $wave_home.Text = '波形'
    $wave_home.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
    $wave_home.Controls.Add($script:wave_slider_panel)
    $wave_home.Controls.Add($script:wave_panel)
    $wave_home.Controls.Add($script:wave_splitter)
    $wave_home.Controls.Add($script:wave_channel_flow)
    $wave_home.Controls.Add($wave_statusbar)
    $wave_home.Controls.Add($wave_toolbar)
    $script:wave_tabs.Controls.Add($wave_home)
    $script:wave_form.Controls.Add($script:wave_tabs)

    # 懒加载 + 崩溃隔离：插件页签首次切入才构建；构建异常只移除该页签并退回波形页
    $script:wave_tabs.Add_SelectedIndexChanged({
        param($sender, $event_args)
        $page = $sender.SelectedTab
        if (-not $page -or -not $page.Tag -or $page.Tag.Built) {
            return
        }
        $page.Tag.Built = $true
        try {
            & $page.Tag.Panel $page
        }
        catch {
            write_runtime_log -message ('插件执行异常 {0}:{1}行: {2}' -f (Split-Path -Leaf $_.InvocationInfo.ScriptName), $_.InvocationInfo.ScriptLineNumber, $_.Exception.Message)
            [void]$sender.Controls.Remove($page)
            $page.Dispose()
            if ($sender.Controls.Count -gt 0) {
                $sender.SelectedIndex = 0
            }
            [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '插件异常', 'OK', 'Error') | Out-Null
        }
    })

    ensure_wave_channel_checks

    $script:wbutton_sim.Add_Click({
        if ($script:wave_sim_on) {
            stop_wave_sim
        }
        else {
            start_wave_sim
        }
    })

    $script:wbutton_copyfmt.Add_Click({
        try {
            $doc_text = get_ai_format_doc -protocol ([string]$script:wcombo_protocol.SelectedItem)
            [System.Windows.Forms.Clipboard]::SetText($doc_text)
            $script:wbutton_copyfmt.Text = '已复制 ✓'
        }
        catch {
            $script:wbutton_copyfmt.Text = '复制失败'
        }

        # 按钮文字短暂反馈后恢复
        $restore_timer = New-Object System.Windows.Forms.Timer
        $restore_timer.Interval = 900
        $restore_timer.Add_Tick({
            if ($script:wbutton_copyfmt -and -not $script:wbutton_copyfmt.IsDisposed) {
                $script:wbutton_copyfmt.Text = '复制AI格式'
            }
            try {
                $restore_timer.Stop()
                $restore_timer.Dispose()
            }
            catch {
            }
        })
        $restore_timer.Start()
    })

    $script:wbutton_auto.Add_Click({
        $script:wave_auto_y = $true
        $script:wcheck_autoy.Checked = $true
        $script:wave_window_frames = 1000
        $script:wcombo_window.Text = '1000'
        $script:wave_view_start = -1
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            $script:wave_panel.Invalidate()
        }
    })

    $script:wcombo_protocol.Add_SelectedIndexChanged({
        $new_protocol = [string]$script:wcombo_protocol.SelectedItem
        if ($new_protocol -and $new_protocol -ne $script:wave_protocol) {
            $script:wave_protocol = $new_protocol
            reset_wave_buffers
            reset_wave_decoder
            $script:wave_hint_last_rx = $script:rx_bytes_total
            $script:wave_hint_last_frames = $script:wave_frame_total
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })

    # 滤波切换：缓存键自动失效，重绘即对旧数据全量生效
    $script:wcombo_filter.Add_SelectedIndexChanged({
        $mode = [string]$script:wcombo_filter.SelectedItem
        if ($mode -and $mode -ne $script:wave_filter_mode) {
            $script:wave_filter_mode = $mode
            $script:wtext_filter_param.Enabled = ($mode -ne '关')
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })

    $script:wtext_filter_param.Add_TextChanged({
        $value = 0
        if ([int]::TryParse($script:wtext_filter_param.Text.Trim(), [ref]$value) -and $value -ge 2 -and $value -ne [int]$script:wave_filter_param) {
            $script:wave_filter_param = $value
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })

    $script:wbutton_format.Add_Click({
        show_wave_format_dialog
    })

    $script:wbutton_record.Add_Click({
        if ($script:session_record_writer) {
            stop_session_record
            $script:wbutton_record.Text = '录制'
            set_wave_button_active -button $script:wbutton_record -active $false
        }
        else {
            start_session_record
            if ($script:session_record_writer) {
                $script:wbutton_record.Text = '停止录制'
                set_wave_button_active -button $script:wbutton_record -active $true
            }
        }
    })

    $script:wbutton_pause.Add_Click({
        $script:wave_paused = -not $script:wave_paused
        if ($script:wave_paused) {
            $script:wbutton_pause.Text = '▶ 继续'
        }
        else {
            $script:wbutton_pause.Text = '⏸ 暂停'
            $script:wave_last_painted_frame = -1
        }
        set_wave_pause_badge
    })

    $script:wbutton_clear.Add_Click({
        reset_wave_buffers
    })

    # ---- 场景预设：切换 / 保存 / 删除 ----
    $script:wcombo_preset.Add_SelectedIndexChanged({
        if ($script:wcombo_preset.SelectedIndex -ge 0) {
            apply_wave_preset -name ([string]$script:wcombo_preset.SelectedItem) | Out-Null
        }
    })

    $script:wbutton_preset_save.Add_Click({
        $default_name = [string]$script:wcombo_preset.Text
        if ([string]::IsNullOrWhiteSpace($default_name)) { $default_name = '新预设' }
        $name = [Microsoft.VisualBasic.Interaction]::InputBox('预设名称（保存当前的 波特率/协议/滤波/视图）：', '保存预设', $default_name)
        if ([string]::IsNullOrWhiteSpace($name)) { return }
        $name = $name.Trim()
        $presets = New-Object 'System.Collections.Generic.List[object]'
        foreach ($p in @(load_wave_presets)) {
            if ($p.Name -ne $name) { [void]$presets.Add($p) }
        }
        $np = capture_wave_preset
        $np.Name = $name
        [void]$presets.Add($np)
        save_wave_presets -presets $presets.ToArray()
        refresh_wave_preset_combo -select $name
    })

    $script:wbutton_preset_del.Add_Click({
        $name = [string]$script:wcombo_preset.SelectedItem
        if ([string]::IsNullOrWhiteSpace($name)) { return }
        $presets = New-Object 'System.Collections.Generic.List[object]'
        foreach ($p in @(load_wave_presets)) {
            if ($p.Name -ne $name) { [void]$presets.Add($p) }
        }
        save_wave_presets -presets $presets.ToArray()
        refresh_wave_preset_combo
    })

    $script:wbutton_allch.Add_Click({
        $any_unchecked = $false
        foreach ($checkbox in $script:wave_channel_checks) {
            if (-not $checkbox.Checked) {
                $any_unchecked = $true
                break
            }
        }

        if ($any_unchecked) {
            foreach ($checkbox in $script:wave_channel_checks) { $checkbox.Checked = $true }
            $script:wbutton_allch.Text = '全不选'
        }
        else {
            foreach ($checkbox in $script:wave_channel_checks) { $checkbox.Checked = $false }
            $script:wbutton_allch.Text = '全选'
        }
    })

    $script:wcheck_autoy.Add_CheckedChanged({
        $script:wave_auto_y = $script:wcheck_autoy.Checked
        $script:wtext_ymin.Enabled = -not $script:wave_auto_y
        $script:wtext_ymax.Enabled = -not $script:wave_auto_y
        if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
            $script:wave_panel.Invalidate()
        }
    })

    $script:wtext_ymin.Add_KeyDown({
        param($sender, $event_args)
        if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
            $event_args.SuppressKeyPress = $true
            apply_wave_y_manual
        }
    })
    $script:wtext_ymin.Add_Leave({ apply_wave_y_manual })

    $script:wtext_ymax.Add_KeyDown({
        param($sender, $event_args)
        if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
            $event_args.SuppressKeyPress = $true
            apply_wave_y_manual
        }
    })
    $script:wtext_ymax.Add_Leave({ apply_wave_y_manual })

    $script:wcombo_window.Add_TextChanged({
        try {
            $new_window = [int]($script:wcombo_window.Text.Trim())
        }
        catch {
            return
        }
        if ($new_window -ne [int]$script:wave_window_frames) {
            set_wave_view_width -new_width $new_window -total $script:wave_frame_count
            if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                $script:wave_panel.Invalidate()
            }
        }
    })

    $script:wbutton_csv.Add_Click({
        show_wave_export_dialog
    })

    $script:wave_paint_timer = New-Object System.Windows.Forms.Timer
    $script:wave_paint_timer.Interval = 66
    $script:wave_paint_timer.Add_Tick({
        try {
            $now = [datetime]::Now
            if (($now - $script:wave_fps_reset_at).TotalMilliseconds -ge 1000) {
                $script:wave_fps_value = $script:wave_fps_count
                $script:wave_fps_count = 0
                $script:wave_fps_reset_at = $now
                if ($script:wlabel_stats) {
                    $rx_delta = $script:rx_bytes_total - $script:wave_hint_last_rx
                    $script:wave_hint_last_rx = $script:rx_bytes_total
                    $frame_delta = $script:wave_frame_total - $script:wave_hint_last_frames
                    $script:wave_hint_last_frames = $script:wave_frame_total

                    $stats_text = ('帧 {0} | {1} 帧/s | 窗口 {2} | 忽略 {3}' -f
                        $script:wave_frame_count,
                        $script:wave_fps_value,
                        [Math]::Min($script:wave_frame_count, $script:wave_window_frames),
                        $script:wave_fail_total)
                    if ($script:wave_view_start -ge 0) {
                        $stats_text += ' | 历史浏览'
                    }
                    if ($script:connected -and ($rx_delta -gt 2048) -and ($frame_delta -eq 0)) {
                        $stats_text += ' | 疑似协议不匹配！请核对「格式说明」'
                    }
                    $script:wlabel_stats.Text = $stats_text

                    if ($script:wave_water_panel -and -not $script:wave_water_panel.IsDisposed) {
                        $script:wave_water_panel.Invalidate()
                    }
                    set_wave_pause_badge
                }
            }

            if (-not $script:wave_paused -and ($script:wave_last_painted_frame -ne $script:wave_frame_total)) {
                $script:wave_last_painted_frame = $script:wave_frame_total
                if ($script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
                if ($script:wave_slider_panel -and -not $script:wave_slider_panel.IsDisposed) {
                    $script:wave_slider_panel.Invalidate()
                }
            }

            # XY 余晖：按真实时间推进（衰减 + 追加新帧）。
            # 数据停流后轨迹也要继续淡出，所以这里无条件重绘，不等新帧。
            if ($script:wave_xy_on) {
                update_wave_xy_accum -now $now
                if (-not $script:wave_paused -and $script:wave_panel -and -not $script:wave_panel.IsDisposed) {
                    $script:wave_panel.Invalidate()
                }
            }
        }
        catch {
            write_runtime_log -message ('波形刷新异常: {0}' -f $_.Exception.Message)
        }
    })

    # ---- 快捷键（输入框/下拉获焦时不抢占）：空格=暂停/继续，Ctrl+E=导出 ----
    $script:wave_form.Add_KeyDown({
        param($sender, $event_args)
        try {
            $active = $sender.ActiveControl
            if ($active -is [System.Windows.Forms.TextBoxBase] -or $active -is [System.Windows.Forms.ComboBox]) {
                return
            }
            if ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::Space) {
                $event_args.SuppressKeyPress = $true
                if ($script:wbutton_pause -and -not $script:wbutton_pause.IsDisposed) {
                    $script:wbutton_pause.PerformClick()
                }
            }
            elseif ($event_args.KeyCode -eq [System.Windows.Forms.Keys]::E -and $event_args.Control) {
                $event_args.SuppressKeyPress = $true
                show_wave_export_dialog
            }
        }
        catch {
        }
    })

    $script:wave_form.Add_FormClosing({
        stop_wave_sim
        if ($script:wave_paint_timer) {
            try {
                $script:wave_paint_timer.Stop()
                $script:wave_paint_timer.Dispose()
            }
            catch {
            }
            $script:wave_paint_timer = $null
        }
        # XY 余晖累积层随窗口销毁（开关/源选择保留，重开窗口自动恢复）
        dispose_wave_xy_bitmaps
        $script:wave_xy_section = $null
        $script:wave_form = $null
        $script:wave_tabs = $null
        $script:wave_panel = $null
        $script:wave_channel_flow = $null
        $script:wave_enabled = $false
    })

    $script:wave_enabled = $true
    $script:wave_paint_timer.Start()
    load_wave_plugins
    refresh_wave_preset_combo
    [void]$script:wave_form.Show($form)
    enable_dark_title_bar -form $script:wave_form
}

