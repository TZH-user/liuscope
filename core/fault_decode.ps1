# core\fault_decode.ps1 — STM32 HardFault 寄存器 dump 解码
# 由 tools\split_modules.ps1 从 serial_terminal_v6.ps1 机械拆分而来（commit 3-5）。
# 纯移动、零行为变化：函数体未改一字，仅按出现顺序搬迁。
# 加载方式：入口 dot-source（. $path），函数在脚本作用域，可直接访问入口的 $script:* 状态与控件变量。
# 依赖：入口在完成 $script: 状态初始化之后加载本文件；本文件内函数之间按出现顺序定义。

function test_fault_dump_line {
    param([string]$text)

    if ([string]::IsNullOrEmpty($text) -or $text -notmatch 'CFSR|HFSR') {
        return $null
    }

    $cfsr = $null
    $hfsr = $null
    $mmfar = $null
    $bfar = $null
    if ($text -match 'CFSR[:=]\s*0x([0-9A-Fa-f]{1,8})') { $cfsr = [Convert]::ToUInt32($Matches[1], 16) }
    if ($text -match 'HFSR[:=]\s*0x([0-9A-Fa-f]{1,8})') { $hfsr = [Convert]::ToUInt32($Matches[1], 16) }
    if ($text -match 'MMFAR[:=]\s*0x([0-9A-Fa-f]{1,8})') { $mmfar = [Convert]::ToUInt32($Matches[1], 16) }
    if ($text -match 'BFAR[:=]\s*0x([0-9A-Fa-f]{1,8})') { $bfar = [Convert]::ToUInt32($Matches[1], 16) }
    if ($null -eq $cfsr -and $null -eq $hfsr) {
        return $null
    }

    $reasons = @()
    if ($null -ne $cfsr) {
        $mmfsr = $cfsr -band 0xFF
        $bfsr = ($cfsr -shr 8) -band 0xFF
        $ufsr = ($cfsr -shr 16) -band 0xFFFF
        if ($mmfsr -band 0x01) { $reasons += 'MMFSR.IACCVIOL 取指访问违例(MPU/XN区域)' }
        if ($mmfsr -band 0x02) { $reasons += 'MMFSR.DACCVIOL 数据访问违例(MPU)' }
        if ($mmfsr -band 0x08) { $reasons += 'MMFSR.MUNSTKERR 出栈时MemManage错误' }
        if ($mmfsr -band 0x10) { $reasons += 'MMFSR.MSTKERR 入栈时MemManage错误' }
        if ($mmfsr -band 0x20) { $reasons += 'MMFSR.MLSPERR 惰性状态保存时错误' }
        if (($mmfsr -band 0x80) -and ($null -ne $mmfar)) { $reasons += ('MMFSR.MMARVALID 出错地址 MMFAR=0x{0:X8}' -f $mmfar) }
        if ($bfsr -band 0x01) { $reasons += 'BFSR.IBUSERR 取指总线错误' }
        if ($bfsr -band 0x02) { $reasons += 'BFSR.PRECISERR 精确数据总线错误' }
        if ($bfsr -band 0x04) { $reasons += 'BFSR.IMPRECISERR 非精确数据总线错误(常见于写缓冲)' }
        if ($bfsr -band 0x08) { $reasons += 'BFSR.UNSTKERR 出栈时总线错误' }
        if ($bfsr -band 0x10) { $reasons += 'BFSR.STKERR 入栈时总线错误' }
        if ($bfsr -band 0x20) { $reasons += 'BFSR.LSPERR 惰性状态保存总线错误' }
        if (($bfsr -band 0x80) -and ($null -ne $bfar)) { $reasons += ('BFSR.BFARVALID 出错地址 BFAR=0x{0:X8}' -f $bfar) }
        if ($ufsr -band 0x0001) { $reasons += 'UFSR.UNDEFINSTR 未定义指令' }
        if ($ufsr -band 0x0002) { $reasons += 'UFSR.INVSTATE 状态无效(常见:函数指针错误/Thumb位)' }
        if ($ufsr -band 0x0004) { $reasons += 'UFSR.INVPC 无效PC加载(返回地址错)' }
        if ($ufsr -band 0x0008) { $reasons += 'UFSR.NOCP 调用无协处理器(常见:未使能FPU却用浮点)' }
        if ($ufsr -band 0x0100) { $reasons += 'UFSR.UNALIGNED 非对齐访问' }
        if ($ufsr -band 0x0200) { $reasons += 'UFSR.DIVBYZERO 除零(SDIV/UDIV)' }
    }
    if ($null -ne $hfsr) {
        if ($hfsr -band 0x00000002) { $reasons += 'HFSR.VECTTBL 取异常向量表时总线错误' }
        if ($hfsr -band 0x40000000) { $reasons += 'HFSR.FORCED 强制升级为HardFault(具体原因查CFSR)' }
    }
    if ($reasons.Count -eq 0) {
        $reasons += 'CFSR/HFSR 各错误位均未置位'
    }

    $cfsr_text = $null
    $hfsr_text = $null
    if ($null -ne $cfsr) { $cfsr_text = '0x{0:X8}' -f $cfsr }
    if ($null -ne $hfsr) { $hfsr_text = '0x{0:X8}' -f $hfsr }

    return [pscustomobject]@{
        cfsr = $cfsr_text
        hfsr = $hfsr_text
        decoded = ($reasons -join '；')
    }
}

