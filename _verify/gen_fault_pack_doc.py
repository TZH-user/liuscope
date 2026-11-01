# -*- coding: utf-8 -*-
"""从 docs/fault_pack/适配指南.md 生成 qt/core/fault_pack_doc.py 的内嵌副本。

冻结 exe 不带 docs/ 目录，故障诊断页的「适配指南」按钮离线可看靠内嵌副本。
仓库 md 为唯一正本——指南改动后重跑：python -X utf8 _verify/gen_fault_pack_doc.py
（selftest 会断言内嵌副本含契约行，防生成遗漏。）
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MD = ROOT / 'docs' / 'fault_pack' / '适配指南.md'
OUT = ROOT / 'qt' / 'core' / 'fault_pack_doc.py'

HEAD = '''# -*- coding: utf-8 -*-
"""!FAULT 适配教程的内嵌文本（冻结 exe 不带 docs/，供故障诊断页一键查看/复制）。

GUIDE_TEXT 由 _verify/gen_fault_pack_doc.py 从 docs/fault_pack/适配指南.md 生成
（仓库 md 为唯一正本，改指南后重新生成）；运行时优先读磁盘正本，
读不到（冻结 exe）才用内嵌副本。本文件机器生成，勿手改。
"""

DISK_REL = ('docs', 'fault_pack', '适配指南.md')

COPY_SNIPPET = (
    "LiuScope !FAULT 契约（一行文本，宽容解析）：\\n"
    "!FAULT T=<HardFault|MemManage|BusFault|UsageFault> PC=0x… LR=0x… XPSR=0x… "
    "CFSR=0x… HFSR=0x… BFAR=0x… MMFAR=0x…\\r\\n\\n"
    "接入三步：\\n"
    "1. 复制 fault_pack/fault_dump.c + .h 进工程（CubeIDE: Core/Src、Core/Inc）；\\n"
    "2. 删除 stm32xxxx_it.c 里 CubeMX 生成的四个空 fault handler（包内已有同名实现）；\\n"
    "3. main 里三行：\\n\\n"
    "static void fault_uart_out(const char *s, uint32_t len) {\\n"
    "  extern UART_HandleTypeDef huart1;                    /* 换成你的串口 */\\n"
    "  if (huart1.gState != HAL_UART_STATE_READY)\\n"
    "    huart1.gState = HAL_UART_STATE_READY;              /* 故障可能停在 HAL 发送里 */\\n"
    "  (void)HAL_UART_Transmit(&huart1, (const uint8_t *)s, (uint16_t)len, 100U);\\n"
    "  uint32_t t = 1000000U;                               /* 等 TC，字节全移出再复位 */\\n"
    "  while (((huart1.Instance->ISR & USART_ISR_TC) == 0U) && (--t != 0U)) { }\\n"
    "}\\n"
    "  fault_dump_set_output(fault_uart_out);\\n"
    "  fault_dump_init();                                   /* 使能三路可屏蔽 fault */\\n\\n"
    "验证：挂 fault_test_trigger() 到控制台命令 → 期望 OK 回执 → !FAULT 行 → 自动复位。\\n"
    "完整教程（可移植性矩阵/FAQ）：docs/fault_pack/适配指南.md\\n"
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

'''  # 生成文件在下方续 GUIDE_TEXT = <repr>；repr 保证任意 md 内容安全嵌入


def main():
    md = MD.read_text(encoding='utf-8')
    OUT.write_text(HEAD + '\nGUIDE_TEXT = ' + repr(md) + '\n', encoding='utf-8')
    print(f'generated {OUT.name}: GUIDE_TEXT {len(md)} chars from {MD.name}')


if __name__ == '__main__':
    main()
