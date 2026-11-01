/**
  * @file    fault_dump.c
  * @brief   真故障自捕获上报适配包（从 H743 台架 V4.3.0 真机验证实现提炼）。
  *
  * 设计纪律（fault 上下文不可信，全部为此让路）：
  *   - 零 malloc、无浮点 printf（仅 %08lX）、小栈占用（~200B）；
  *   - 输出走用户注册的阻塞轮询函数（fault 时中断/DMA 状态不可信）；
  *   - 压栈帧先做合法性检查（weak 钩子可按芯片定制 RAM 区），不可信就降级
  *     为只报 SCB 寄存器（契约允许 PC/LR/XPSR 缺键）；
  *   - fault-in-fault 防护：同一次上电会话第二次进 fault 直接 lockup，防打环；
  *   - 上报完 NVIC_SystemReset() 保活，不依赖看门狗。
  *
  * 内核支持：ARMv7-M / ARMv8-M mainline（Cortex-M3/M4/M7/M33）四路 fault 全量；
  * ARMv6-M（M0/M0+）自动降级为仅 HardFault + PC/LR/XPSR（无 CFSR 等寄存器）。
  * 汇编为 GCC/AC6 语法；AC5/IAR 的 naked 写法见《适配指南.md》§6。
  */
#include "fault_dump.h"
#include <stdio.h>  /* snprintf（ fault 路径只用 %s/%08lX，无浮点） */
#include "main.h" /* CubeMX 工程：main.h 会带出正确的 CMSIS 设备头；其它工程换成设备头 */

#if defined(__ARM_ARCH_7M__) || defined(__ARM_ARCH_7EM__) || \
    defined(__ARM_ARCH_8M_BASE__) || defined(__ARM_ARCH_8M_MAIN__)
#define FAULT_DUMP_V7 1 /* CFSR/HFSR/BFAR/MMFAR 与三路可屏蔽 fault 存在 */
#endif

static fault_dump_output_fn fault_out = 0;

/* fault-in-fault 防护：同一次上电会话只允许上报一次（复位后自动清零） */
static uint32_t fault_dump_count = 0U;

void fault_dump_set_output(fault_dump_output_fn fn)
{
  fault_out = fn;
}

void fault_dump_init(void)
{
#ifdef FAULT_DUMP_V7
  /* 使能三个可配置 fault（HardFault 恒使能，无需开关） */
  SCB->SHCSR |= (SCB_SHCSR_MEMFAULTENA_Msk | SCB_SHCSR_BUSFAULTENA_Msk |
                 SCB_SHCSR_USGFAULTENA_Msk);
#endif
}

/* 压栈帧（R0,R1,R2,R3,R12,LR,PC,XPSR @ sp+0x00..0x1F）合法性检查。
 * 默认覆盖常见 RAM 窗口（CCM/通用 SRAM/H7 AXI/SRAM1-4），按芯片可 override：
 *   extern 改写同名函数即可，例：返回 (sp >= 0x20000000 && sp < 0x20040000)。 */
__attribute__((weak)) uint32_t fault_dump_sp_valid(uint32_t sp)
{
  static const struct { uint32_t lo, hi; } ram_zone[] = {
    {0x10000000U, 0x10010000U}, /* CCM data RAM（F4 等） */
    {0x20000000U, 0x20050000U}, /* 通用 SRAM（F1/F4/L4/G0/G4 至 320K） */
    {0x24000000U, 0x24080000U}, /* AXI SRAM 512K（H7） */
    {0x30000000U, 0x30048000U}, /* SRAM1/2 288K（H7） */
    {0x38000000U, 0x38010000U}, /* SRAM4 64K（H7） */
  };
  if ((sp & 0x7U) != 0U || sp == 0U)
  {
    return 0U;
  }
  for (uint32_t i = 0U; i < sizeof(ram_zone) / sizeof(ram_zone[0]); i++)
  {
    if (sp >= ram_zone[i].lo && (sp + 0x20U) <= ram_zone[i].hi)
    {
      return 1U;
    }
  }
  return 0U;
}

void fault_dump_report(uint32_t t, uint32_t sp)
{
  static const char *const fault_type_name[4] = {
      "HardFault", "MemManage", "BusFault", "UsageFault"};
  char buf[160];
  int n = 0;

  if (fault_dump_count != 0U)
  {
    /* fault-in-fault：上报过程中又出故障，直接 lockup 不再上报（防打环） */
    __disable_irq();
    while (1) { }
  }
  fault_dump_count = 1U;

  if (fault_out == 0)
  {
    NVIC_SystemReset(); /* 未注册输出：无处上报，仍复位保活 */
  }

  const char *tn = (t < 4U) ? fault_type_name[t] : fault_type_name[0];

  if (fault_dump_sp_valid(sp) != 0U)
  {
    volatile const uint32_t *frame = (volatile const uint32_t *)(uint32_t)sp;
    const uint32_t lr = frame[5];
    const uint32_t pc = frame[6];
    const uint32_t xpsr = frame[7];
#ifdef FAULT_DUMP_V7
    const uint32_t cfsr = SCB->CFSR;
    const uint32_t hfsr = SCB->HFSR;
    const uint32_t bfar = SCB->BFAR;
    const uint32_t mmfar = SCB->MMFAR;
    n = snprintf(buf, sizeof(buf),
                 "!FAULT T=%s PC=0x%08lX LR=0x%08lX XPSR=0x%08lX "
                 "CFSR=0x%08lX HFSR=0x%08lX BFAR=0x%08lX MMFAR=0x%08lX\r\n",
                 tn, (unsigned long)pc, (unsigned long)lr,
                 (unsigned long)xpsr, (unsigned long)cfsr,
                 (unsigned long)hfsr, (unsigned long)bfar,
                 (unsigned long)mmfar);
#else
    (void)tn; /* ARMv6-M：无 CFSR 族寄存器，只报压栈帧三键（解析端宽容缺键） */
    n = snprintf(buf, sizeof(buf),
                 "!FAULT T=HardFault PC=0x%08lX LR=0x%08lX XPSR=0x%08lX\r\n",
                 (unsigned long)pc, (unsigned long)lr, (unsigned long)xpsr);
#endif
  }
  else
  {
    /* 压栈帧不可信：PC/LR/XPSR 缺键（解析端宽容），SCB 寄存器仍可见 */
#ifdef FAULT_DUMP_V7
    n = snprintf(buf, sizeof(buf),
                 "!FAULT T=%s CFSR=0x%08lX HFSR=0x%08lX BFAR=0x%08lX "
                 "MMFAR=0x%08lX\r\n",
                 tn, (unsigned long)SCB->CFSR, (unsigned long)SCB->HFSR,
                 (unsigned long)SCB->BFAR, (unsigned long)SCB->MMFAR);
#else
    n = snprintf(buf, sizeof(buf), "!FAULT T=HardFault\r\n");
#endif
  }

  if (n > 0)
  {
    fault_out(buf, (uint32_t)n);
  }

  __DSB();
  __ISB();
  NVIC_SystemReset();
  while (1) { }
}

/* ============================ fault handlers ============================
 * 四个 naked handler：prologue 压栈会移动 MSP，压栈帧 R0-R3/R12/LR/PC/XPSR
 * 就不在固定偏移处——所以必须 naked，判 EXC_RETURN bit2 选 MSP/PSP，把压栈帧
 * 顶传 C 处理器 fault_dump_report(t, sp)。
 * ⚠ 接入时删除/替换 stm32xxxx_it.c 里 CubeMX 生成的同名空 handler（否则重定义）。
 */
__attribute__((naked)) void HardFault_Handler(void)
{
  __asm volatile (
    "tst lr, #4          \n" /* EXC_RETURN bit2: 0=MSP, 1=PSP */
    "ite eq              \n"
    "mrseq r1, msp       \n"
    "mrsne r1, psp       \n"
    "mov r0, #0          \n" /* T=HardFault */
    "b fault_dump_report \n"
  );
}

#ifdef FAULT_DUMP_V7
/* 以下三路需 fault_dump_init() 置 SCB->SHCSR 对应位，否则升级为 HardFault */
__attribute__((naked)) void MemManage_Handler(void)
{
  __asm volatile (
    "tst lr, #4          \n"
    "ite eq              \n"
    "mrseq r1, msp       \n"
    "mrsne r1, psp       \n"
    "mov r0, #1          \n" /* T=MemManage */
    "b fault_dump_report \n"
  );
}

__attribute__((naked)) void BusFault_Handler(void)
{
  __asm volatile (
    "tst lr, #4          \n"
    "ite eq              \n"
    "mrseq r1, msp       \n"
    "mrsne r1, psp       \n"
    "mov r0, #2          \n" /* T=BusFault */
    "b fault_dump_report \n"
  );
}

__attribute__((naked)) void UsageFault_Handler(void)
{
  __asm volatile (
    "tst lr, #4          \n"
    "ite eq              \n"
    "mrseq r1, msp       \n"
    "mrsne r1, psp       \n"
    "mov r0, #3          \n" /* T=UsageFault */
    "b fault_dump_report \n"
  );
}
#endif /* FAULT_DUMP_V7 */

/* ============================ 可选自测钩子 ============================
 * 故意触发一次真故障供联调验收（详见《适配指南.md》§5）：
 * 非对齐 32 位读 + UNALIGN_TRP 陷阱 -> UsageFault(UFSR.UNALIGNED)。
 * 期望捕获行 T=UsageFault CFSR=0x01000000，PC 落在本函数内。 */
void fault_test_trigger(void)
{
  static uint32_t fault_test_probe = 0xA5A5A5A5U; /* 编译器对齐，地址 4 对齐 */
  /* +1 构造非对齐地址；volatile 保证编译器生成单条 LDR 不优化掉 */
  volatile uint32_t *unaligned =
      (volatile uint32_t *)(uint32_t)((uint32_t)&fault_test_probe + 1U);

#ifdef FAULT_DUMP_V7
  SCB->CCR |= SCB_CCR_UNALIGN_TRP_Msk; /* 非对齐访问陷入 -> UsageFault */
  __DSB();
  __ISB();
#endif
  (void)*unaligned; /* ARMv6-M 非对齐访问本身即 HardFault；不应到达下一行 */

  /* 未陷入说明环境异常：原地停住，不伪造故障行（!FAULT 只能来自真异常） */
  __disable_irq();
  while (1) { }
}
