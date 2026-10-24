# plugins\_template\plugin.example.ps1 —— 插件模板
#
# 用法：复制为 plugins\<你的插件名>\plugin.ps1 后改写。
# 注意：本文件名以 .example.ps1 结尾，宿主扫描 plugins\*\plugin.ps1 时【不会】加载它，
#       所以放在这里当样板是安全的。
# 编码：UTF-8 带 BOM（PS5.1 对无 BOM 的 UTF-8 按 GBK 解析，中文会乱码）。
#
# 插件 API 版本：1

# ---------- 插件自持状态（一律加前缀，避免与宿主 $script:wave_* 冲突） ----------
$script:tmpl_panel = $null
$script:tmpl_timer = $null

function tmpl_ensure_panel {
    if ($script:tmpl_panel -and -not $script:tmpl_panel.IsDisposed) {
        return $script:tmpl_panel
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = '模板插件'
    $form.Size = New-Object System.Drawing.Size(520, 300)
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = [System.Drawing.Color]::FromArgb(28, 33, 45)
    $form.ForeColor = [System.Drawing.Color]::FromArgb(225, 230, 240)

    $label = New-Object System.Windows.Forms.Label
    $label.Dock = 'Top'
    $label.Height = 64
    $label.TextAlign = 'MiddleCenter'
    $label.Text = '这里是插件自己的面板（示例：读取通道 0 最近 256 帧）'
    $form.Controls.Add($label)

    $grid = New-Object System.Windows.Forms.DataGridView
    $grid.Dock = 'Fill'
    $grid.ReadOnly = $true
    $grid.RowHeadersVisible = $false
    $grid.AllowUserToAddRows = $false
    $grid.ColumnCount = 2
    $grid.Columns[0].Name = 'index'
    $grid.Columns[1].Name = 'value'
    $form.Controls.Add($grid)

    # 自持刷新：不用宿主事件，避免影响宿主重绘
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 500
    $timer.Add_Tick({
        try {
            if ($grid.IsDisposed -or $form.IsDisposed) { return }
            $values = Get-WavePluginSnapshot -Channel 0 -Count 8
            $grid.Rows.Clear()
            if ($values -and $values.Count -gt 0) {
                for ($i = 0; $i -lt $values.Count; $i++) {
                    [void]$grid.Rows.Add($i, [Math]::Round([double]$values[$i], 4))
                }
            }
        }
        catch {
            # 插件内部错误自行吸收，不外抛；外抛只会导致插件被禁用
            $label.Text = ('刷新失败: ' + $_.Exception.Message)
        }
    })
    $timer.Start()
    $script:tmpl_timer = $timer

    # 关闭即释放，避免残留窗口与计时器
    $form.Add_FormClosing({
        if ($script:tmpl_timer) {
            $script:tmpl_timer.Stop()
            $script:tmpl_timer.Dispose()
            $script:tmpl_timer = $null
        }
        $script:tmpl_panel = $null
    })

    $script:tmpl_panel = $form
    return $form
}

# ---------- 注册（顶层唯一副作用） ----------
Register-WavePlugin -Name '模板' -Description '插件开发样板：读取通道 0 最近数据' -OpenPanel {
    $panel = tmpl_ensure_panel
    if (-not $panel.Visible) { $panel.Show() }
    else { $panel.BringToFront() }
}
