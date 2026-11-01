/**
  * @file    fault_dump.h
  * @brief   真故障自捕获上报适配包（LiuScope「故障诊断」页配套固件端，单文件接入）
  *
  * 契约（LiuScope 宽容解析）：故障时向串口打一行
  *   !FAULT T=<类型> PC=0x… LR=0x… XPSR=0x… CFSR=0x… HFSR=0x… BFAR=0x… MMFAR=0x…
  * 以 \r\n 结尾，随后 NVIC_SystemReset()。缺键允许（压栈帧不可信时自动降级）、
  * 未知键保留、截断的 hex 值（1~8 位）可解析——半行也能进面板。
  *
  * 接入三步（详见同目录《适配指南.md》）：
  *   1. 本文件 + fault_dump.c 复制进工程；
  *   2. 删除/替换 stm32xxxx_it.c 中 CubeMX 生成的四个空 fault handler；
  *   3. main 初始化：fault_dump_set_output(阻塞发送函数) + fault_dump_init()。
  * PC 侧：LiuScope「故障诊断」页把 ELF 路径指到本工程 .elf（可选，指了才出函数名）。
  */
#ifndef FAULT_DUMP_H
#define FAULT_DUMP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 阻塞输出函数：故障上下文里会被调用一次，必须轮询发送不依赖中断/DMA。
 * 推荐包装 HAL_UART_Transmit（见适配指南 §3，含 gState/TC 两个坑的处理）。 */
typedef void (*fault_dump_output_fn)(const char *data, uint32_t len);

/* 注册输出函数（main 里调用一次；不注册则只复位不上报） */
void fault_dump_set_output(fault_dump_output_fn fn);

/* 使能 MemManage/BusFault/UsageFault（HardFault 恒使能）；main 里调用一次 */
void fault_dump_init(void);

/* 跳板目标（fault_handlers 的 naked 汇编分支到此，不返回） */
void fault_dump_report(uint32_t t, uint32_t sp);

/* 可选自测：故意触发一次非对齐 UsageFault（见适配指南 §5 验证流程） */
void fault_test_trigger(void);

#ifdef __cplusplus
}
#endif
#endif /* FAULT_DUMP_H */
