# -*- coding: utf-8 -*-
"""!FAULT 适配教程的内嵌文本（冻结 exe 不带 docs/，供故障诊断页一键查看/复制）。

GUIDE_TEXT 由 _verify/gen_fault_pack_doc.py 从 docs/fault_pack/适配指南.md 生成
（仓库 md 为唯一正本，改指南后重新生成）；运行时优先读磁盘正本，
读不到（冻结 exe）才用内嵌副本。本文件机器生成，勿手改。
"""

DISK_REL = ('docs', 'fault_pack', '适配指南.md')

COPY_SNIPPET = (
    "LiuScope !FAULT 契约（一行文本，宽容解析）：\n"
    "!FAULT T=<HardFault|MemManage|BusFault|UsageFault> PC=0x… LR=0x… XPSR=0x… "
    "CFSR=0x… HFSR=0x… BFAR=0x… MMFAR=0x…\r\n\n"
    "接入三步：\n"
    "1. 复制 fault_pack/fault_dump.c + .h 进工程（CubeIDE: Core/Src、Core/Inc）；\n"
    "2. 删除 stm32xxxx_it.c 里 CubeMX 生成的四个空 fault handler（包内已有同名实现）；\n"
    "3. main 里三行：\n\n"
    "static void fault_uart_out(const char *s, uint32_t len) {\n"
    "  extern UART_HandleTypeDef huart1;                    /* 换成你的串口 */\n"
    "  if (huart1.gState != HAL_UART_STATE_READY)\n"
    "    huart1.gState = HAL_UART_STATE_READY;              /* 故障可能停在 HAL 发送里 */\n"
    "  (void)HAL_UART_Transmit(&huart1, (const uint8_t *)s, (uint16_t)len, 100U);\n"
    "  uint32_t t = 1000000U;                               /* 等 TC，字节全移出再复位 */\n"
    "  while (((huart1.Instance->ISR & USART_ISR_TC) == 0U) && (--t != 0U)) { }\n"
    "}\n"
    "  fault_dump_set_output(fault_uart_out);\n"
    "  fault_dump_init();                                   /* 使能三路可屏蔽 fault */\n\n"
    "验证：挂 fault_test_trigger() 到控制台命令 → 期望 OK 回执 → !FAULT 行 → 自动复位。\n"
    "完整教程（可移植性矩阵/FAQ）：docs/fault_pack/适配指南.md\n"
)


def disk_path():
    """仓库内指南正本路径（相对本模块上两级 = 仓库根）。"""
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *DISK_REL)


def guide_text():
    """优先磁盘正本，读不到（冻结 exe）退内嵌副本。"""
    try:
        with open(disk_path(), encoding='utf-8') as f:
            return f.read()
    except OSError:
        return GUIDE_TEXT


GUIDE_TEXT = '# !FAULT 故障上报固件适配指南（一键接入包）\n\n> **定位**：让**任何** STM32（及其它 ARMv7-M 内核）固件在真崩溃时从串口自报一条\n> `!FAULT` 行，LiuScope「故障诊断」页即全功能识别——树形寄存器位域 + ELF 符号化\n> （函数名+偏移）+ 历史记录 + 状态栏徽标。无需探针/仿真器在场，适合装机、远程、\n> 通宵等无人值守场合；与调试器故障视图（要 halt 才能看）互补，不重复。\n>\n> **真机验证基准**：H743 台架 V4.3.0（2026-11-01 验收：mode_test 16/16、\n> FAULTTEST→UsageFault 捕获→符号化 `fault_test_trigger+0x25`）。本包由该实现提炼，\n> 已通过 cortex-m7 / cortex-m0 双档 GCC 13.3 编译（-Wall -Wextra 0 警告）。\n\n---\n\n## 1. 契约规格（就一行文本）\n\n```text\n!FAULT T=UsageFault PC=0x08008DEA LR=0x0800836B XPSR=0x01000000 CFSR=0x01000000 HFSR=0x00000000 BFAR=0x00000000 MMFAR=0x00000000\\r\\n\n```\n\n| 键 | 含义 | 缺失时 |\n|---|---|---|\n| `T` | HardFault / MemManage / BusFault / UsageFault | 必有 |\n| `PC` | 故障指令地址（压栈帧）→ 符号化的主角 | 显示裸地址不符号化 |\n| `LR` | 返回地址（谁调用进来的） | 同上 |\n| `XPSR` | 程序状态寄存器 | — |\n| `CFSR` | 故障状态寄存器（位域树的主数据源） | 位域树缺该组 |\n| `HFSR` | 硬故障寄存器（DEBUGEVT/FORCED/VECTTBL） | 同上 |\n| `BFAR` / `MMFAR` | 总线/内存管理故障地址 | 显示 0x0 + "无效" |\n\n**解析端宽容规则**（LiuScope 已实现，固件按本指南打行即可命中）：\n半行可收（串口断在中间不丢整条）、未知键保留、缺键渲染 "--" 不猜值、\n截断 hex（1~8 位）可解析。**打完必须跟 `\\r\\n` 并随后 `NVIC_SystemReset()`**。\n\n## 2. 第一步：复制两个文件\n\n`fault_dump.c` → 工程源码目录（CubeIDE 工程放 `Core/Src/`），`fault_dump.h` → `Core/Inc/`。\n\n非 CubeMX 工程把 `fault_dump.c` 里的 `#include "main.h"` 换成你的设备头\n（如 `stm32f407xx.h`）；CubeMX 工程不用动（`main.h` 会带出正确的 CMSIS 头）。\n\n## 3. 第二步：替换四个空 fault handler\n\n删除 `stm32xxxx_it.c` 里 CubeMX 生成的四个空 handler（包内已有同名实现，不删会重定义）：\n\n- `HardFault_Handler` / `MemManage_Handler` / `BusFault_Handler` / `UsageFault_Handler`\n- 只想要 HardFault 也行：把包内三路可屏蔽 handler 一并删掉即可\n  （但**建议保留**：BusFault 位域信息量大；三路要 `fault_dump_init()` 使能后才会进对应 handler）\n- ⚠ **CubeMX 重跑代码生成会还原 it.c** → 需重删一次；替代做法是把包内四个\n  handler 的函数体贴进 it.c 的同名函数（贴完记得保持 `__attribute__((naked))`）\n\n## 4. 第三步：main 里三行初始化\n\n```c\n/* 用户侧阻塞输出：故障上下文里只被调用一次，必须轮询发送、禁用中断/DMA 路径 */\nstatic void fault_uart_out(const char *s, uint32_t len)\n{\n  extern UART_HandleTypeDef huart1;          /* 换成你的串口 */\n  if (huart1.gState != HAL_UART_STATE_READY) /* 坑1：故障可能正停在 HAL 发送里 */\n    huart1.gState = HAL_UART_STATE_READY;    /*  fault 路径允许强回 READY，随后即复位 */\n  (void)HAL_UART_Transmit(&huart1, (const uint8_t *)s, (uint16_t)len, 100U);\n  uint32_t t = 1000000U;                     /* 坑2：等 TC 字节全移出移位寄存器 */\n  while (((huart1.Instance->ISR & USART_ISR_TC) == 0U) && (--t != 0U)) { }\n}\n\nint main(void)\n{\n  /* ... CubeMX 生成的初始化 ... */\n  fault_dump_set_output(fault_uart_out);  /* 不注册则只复位不上报 */\n  fault_dump_init();                      /* 使能 MemManage/BusFault/UsageFault */\n  /* ... */\n}\n```\n\n两个坑的来历：故障可能发生在**任意**位置——包括 HAL_UART_Transmit 内部\n（gState 卡在 BUSY）；复位前若不等 TC，最后几个字节还留在移位寄存器里会丢。\n\n## 5. 第四步：验证（FAULTTEST 自测钩子）\n\n包内 `fault_test_trigger()` 会故意触发一次**真**非对齐 UsageFault\n（置 UNALIGN_TRP 后做非对齐 32 位读——内核同步抛异常，不是软件模拟）。\n\n1. 把它挂到你自己的控制台命令（或临时在按键里调一次）；\n2. LiuScope 连串口（同波特率）发送该命令，期望四步现象：\n   **OK 回执 → `!FAULT T=UsageFault … CFSR=0x01000000` → 自动复位 → 开机横幅重现**；\n3. 「故障诊断」页 ELF 路径指到本工程 `.elf`（可选但强烈建议）→\n   树顶「故障函数」出 `fault_test_trigger+0x..`，「用法故障」组 UNALIGNED 红色置位；\n4. 也可以直接故意写坏指针验证（比 FAULTTEST 更接近真实事故）。\n\n## 6. 可移植性\n\n| 内核 | 支持度 |\n|---|---|\n| M3 / M4 / M7（ARMv7-M/E） | ✅ 四路 fault 全量（本包默认路径，台架实测 M7） |\n| M33（ARMv8-M mainline） | 理论全量（同 SHCSR/CFSR 寄存器模型），未实测 |\n| M0 / M0+（ARMv6-M） | 自动降级：仅 HardFault + PC/LR/XPSR（无 CFSR 族寄存器，包内宏自动处理；跳板用 Thumb-1 分支版——M0 无 IT 块，与 v7 版语义等价） |\n\n- **Keil AC6**：GNU 属性直接支持，无需改动。**AC5 / IAR**：naked 换写法——\n  AC5 用 `__attribute__((naked))` 也可但需确认版本，IAR 用 `#pragma inline = forced`\n  + `__asm` 块（把 `tst lr,#4 / ite eq / mrs / mov r0,#t / b fault_dump_report` 原样搬）。\n- **RAM 窗口校验**：压栈帧合法性检查默认覆盖 CCM/通用 SRAM/H7 各 RAM 区；\n  其它芯片可在自己工程里写同名函数 override：`uint32_t fault_dump_sp_valid(uint32_t sp)`。\n\n## 7. FAQ\n\n- **没看到 !FAULT**：按顺序查——波特率对不对 → `fault_dump_set_output` 注册了吗 →\n  `fault_dump_init` 调了吗 → 输出函数是不是走了 DMA/中断（fault 态不可信，必须轮询）。\n- **只有裸地址没有函数名**：「故障诊断」页 ELF 路径没指，或指的 .elf 与板上固件\n  不是同一次构建（面板按 mtime 缓存，重编译后自动失效重载）。\n- **故障地址显示"无效"**：IMPRECISERR（写缓冲）时 BFARVALID 不置位，地址寄存器\n  本来就不可信——PC 是精确的（取指精确），照常符号化定位。\n- **为什么打完就复位**：fault 态下内核状态不可信，复位保活最稳；LiuScope 侧半行\n  宽容已处理"上报中途断线感"。要自恢复逻辑请自行改掉 `NVIC_SystemReset()`，风险自担。\n- **会被优化掉吗**：压栈帧读取用 `volatile`、trampoline 用 naked 函数，\n  语义由 C/内核保证不随优化级漂移；台架构建（-Og）已真机验证，\n  本包另以 GCC 13.3 双档编译确认汇编形状。\n'
