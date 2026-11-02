// X910: first look at the battery gauge (SM5714, I2C hub SE8 at 0x9a0000, TLMM GPIO 206/207 function 1, devicetree of the tablet's TWRP).
// The hub has no DMA in that devicetree, so its FIFO is used, by polling, in the order of Linux's i2c-qcom-geni.c at 100 kHz over the
// 19.2 MHz default of the SE clock. Reads only on the bus: each word register is addressed by one byte. The result goes to the message buffer
// (x910fg lines), since which registers hold what is still to be matched against the values TWRP shows.
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IODeviceMemory.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
extern "C" {volatile int32_t x910_fuel_stats[8];}   // [0] stage (negative: the step that failed), [1] SE clock CBCR, [2] FW revision, [3] words read, [4] failures, [5] last M IRQ

namespace {
constexpr uint64_t kGcc = 0x100000, kTlmm = 0xf100000, kSe = 0x9a0000;
constexpr uint32_t kVoteA = 0x52008, kVoteB = 0x52010, kSeCbcr = 0x170e4;
constexpr uint32_t kFw = 0x68, kStatus = 0x40, kClkCfg = 0x48, kClkSel = 0x7c, kCgc = 0x28, kDmaMode = 0x258, kGran = 0x254;
constexpr uint32_t kTxPack0 = 0x260, kTxPack1 = 0x264, kRxPack0 = 0x284, kRxPack1 = 0x288, kTxLen = 0x26c, kRxLen = 0x270, kScl = 0x278;
constexpr uint32_t kCmd = 0x600, kCmdCtrl = 0x604, kIrq = 0x610, kIrqClear = 0x618, kTx = 0x700, kRx = 0x780, kRxStatus = 0x804, kSeIrqEn = 0xe1c, kGsi = 0xe18;
constexpr uint32_t kDone = 1u << 0, kFailure = 1u << 3, kAbort = 1u << 5, kStretch = 1u << 2;
constexpr uint32_t kWrite = 1, kRead = 2;

volatile uint32_t *mapWindow(uint64_t base, uint64_t size) {
    IODeviceMemory *memory = IODeviceMemory::withRange(base, size);
    IOMemoryMap *map = memory ? memory->map(kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
    if (memory) memory->release();
    return map ? reinterpret_cast<volatile uint32_t *>(map->getVirtualAddress()) : nullptr;
}
volatile uint32_t *gSe;
inline volatile uint32_t &reg(uint32_t offset) {return gSe[offset / 4];}
void barrier() {__asm__ volatile("dsb sy" ::: "memory");}

// Waits for the command to end, at most 20 ms (a 100 kHz word takes under a millisecond). Aborts one that does not; false on any failure.
bool finish() {
    for (unsigned i = 0; i < 2000; ++i) {
        const uint32_t irq = reg(kIrq);
        x910_fuel_stats[5] = (int32_t)irq;
        if (irq & (kFailure | kAbort)) {reg(kIrqClear) = irq; return false;}
        if (irq & kDone) {reg(kIrqClear) = irq; return !(irq & ~(kDone | (0xfu << 26)) & 0x3f0u);}
        IODelay(10);
    }
    reg(kCmdCtrl) = 1u << 1;
    barrier();
    IODelay(100);
    reg(kIrqClear) = 0xffffffffu;
    return false;
}
bool readWord(uint32_t address, uint8_t index, uint16_t *out) {
    reg(kIrqClear) = 0xffffffffu;
    reg(kTxLen) = 1;
    reg(kCmd) = (kWrite << 27) | kStretch | (address << 9);
    barrier();
    reg(kTx) = index;
    barrier();
    if (!finish()) return false;
    reg(kRxLen) = 2;
    reg(kCmd) = (kRead << 27) | (address << 9);
    barrier();
    uint32_t word = 0;
    bool got = false;
    for (unsigned i = 0; i < 2000 && !got; ++i) {
        if (reg(kRxStatus) & 0x1ffffffu) {word = reg(kRx); got = true;}
        else if (reg(kIrq) & (kFailure | kAbort)) break;
        else IODelay(10);
    }
    if (!finish() || !got) return false;
    *out = (uint16_t)word;
    return true;
}
void dump(uint32_t address, unsigned first, unsigned count) {
    char line[160];
    for (unsigned at = first; at < first + count; at += 16) {
        int used = snprintf(line, sizeof line, "x910fg %02x %02x:", address, at);
        for (unsigned i = 0; i < 16 && used < (int)sizeof line - 6; ++i) {
            uint16_t value = 0;
            if (readWord(address, (uint8_t)(at + i), &value)) {
                x910_fuel_stats[3] = x910_fuel_stats[3] + 1;
                used += snprintf(line + used, sizeof line - used, " %04x", value);
            } else {
                x910_fuel_stats[4] = x910_fuel_stats[4] + 1;
                used += snprintf(line + used, sizeof line - used, " ----");
            }
        }
        printf("%s\n", line);
    }
}
void fuelThread(void *, wait_result_t) {
    thread_set_thread_name(current_thread(), "x910-fuel");
    IOSleep(40000);
    volatile uint32_t *gcc = mapWindow(kGcc, 0x80000), *tlmm = mapWindow(kTlmm + 206 * 0x1000, 0x2000);
    gSe = mapWindow(kSe, 0x4000);
    if (!gcc || !tlmm || !gSe) {x910_fuel_stats[0] = -1; return;}
    gcc[kVoteA / 4] = gcc[kVoteA / 4] | (1u << 7) | (1u << 8);
    gcc[kVoteB / 4] = gcc[kVoteB / 4] | (1u << 14);
    barrier();
    uint32_t cbcr = 0x80000000u;
    for (unsigned i = 0; i < 100 && (cbcr >> 31); ++i) {cbcr = gcc[kSeCbcr / 4]; IODelay(100);}
    x910_fuel_stats[1] = (int32_t)cbcr;
    // A register of a clockless SE stalls the bus: nothing past this point without the clock.
    if (cbcr >> 31) {x910_fuel_stats[0] = -2; return;}
    x910_fuel_stats[0] = 1;
    x910_fuel_stats[2] = (int32_t)reg(kFw);
    printf("x910fg fw=%x status=%x cbcr=%x gpio=%x/%x dma=%x\n", reg(kFw), reg(kStatus), cbcr, tlmm[0], tlmm[0x1000 / 4], reg(kDmaMode));
    if (((reg(kFw) >> 8) & 0xff) != 3) {x910_fuel_stats[0] = -3; return;}   // protocol 3 is I2C
    // The pins come set up by the boot chain as 0x1807: function 1, pull-up, eGPIO present and enabled (bit 12 keeps the pin with TLMM
    // rather than the low-power island). V14 wrote 0x44 over them, which dropped both, and every read ended in arbitration lost
    // (M_GP_IRQ_4, irq 0x8002081). They are left as found; a pin not on function 1 stops the probe.
    if (((tlmm[0] >> 2) & 0xf) != 1 || ((tlmm[0x1000 / 4] >> 2) & 0xf) != 1) {x910_fuel_stats[0] = -4; return;}
    reg(kCgc) = reg(kCgc) | 0x7f;
    reg(kDmaMode) = reg(kDmaMode) & ~1u;
    reg(kGsi) = 0;
    reg(kSeIrqEn) = reg(kSeIrqEn) | 0xf;
    const uint32_t pack0 = ((0u << 5) | (7u << 1)) | (((8u << 5) | (7u << 1)) << 10);
    const uint32_t pack1 = ((16u << 5) | (7u << 1)) | (((24u << 5) | (7u << 1) | 1u) << 10);
    reg(kTxPack0) = pack0; reg(kTxPack1) = pack1;
    reg(kRxPack0) = pack0; reg(kRxPack1) = pack1;
    reg(kGran) = 0;
    reg(kClkSel) = 0;
    reg(kClkCfg) = (7u << 4) | 1u;
    reg(kScl) = (10u << 20) | (11u << 10) | 26u;
    barrier();
    x910_fuel_stats[0] = 2;
    for (unsigned round = 0; round < 2; ++round) {
        dump(0x71, 0x00, 0x20);
        dump(0x71, 0x80, 0x10);
        dump(0x49, 0x00, 0x10);
        IOSleep(60000);
    }
    x910_fuel_stats[0] = 3;
}
}  // namespace

extern "C" void x910_fuel_probe(void) {
    // On unless x910-fg=0. V14's hang was its own test panic held on the panic screen, not this probe.
    uint32_t enabled = 1;
    if (PE_parse_boot_argn("x910-fg", &enabled, sizeof(enabled)) && enabled == 0) return;
    thread_t thread = nullptr;
    if (kernel_thread_start(fuelThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    else x910_fuel_stats[0] = -9;
}
