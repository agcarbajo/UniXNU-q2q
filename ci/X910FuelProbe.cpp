// X910: the battery as iOS reads it. The SM5714's fuel gauge (0x71) and charger (0x49) sit on I2C hub SE8 at 0x9a0000, TLMM GPIO 206/207
// function 1, as the tablet's TWRP devicetree has them. The hub has no DMA in that devicetree, so its FIFO is used, by polling, in the order
// of Linux's i2c-qcom-geni.c at 100 kHz over the 19.2 MHz default of the SE clock. The gauge's values are behind its SRAM window: the SRAM
// address goes to 0x8c and the word comes from 0x8d, conversions and addresses as the owner's sm5714_battery.c (ubuntu-galaxy-tab-s9-ultra)
// and Samsung's sm5714_fuelgauge.c have them. The one register written is that window address; charging itself stays the charger's own.
//
// The values go to a plain IOPMPowerSource (no subclass: its setters are public) attached to the platform expert, which is what powerd
// matches; the status bar, the lock screen's charging state and Settings > Battery follow from its CurrentCapacity, IsCharging and
// ExternalConnected, as upstream sheng's ShengBattery.cpp publishes them. It is registered only after a full reading, so no reader ever
// sees an empty battery, and refreshed every 30 s by this thread. x910-fg=0 turns all of it off.
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/pwr_mgt/IOPMPowerSource.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSSymbol.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
// [0] stage (negative: the step that failed; 4 published), [1] SE clock CBCR, [2] FW revision, [3] words read, [4] failures, [5] last M IRQ,
// [6] percent, [7] mV | charger status bits << 16 (bit 0 VBUS present, 1 charging, 2 top-off)
extern "C" {volatile int32_t x910_fuel_stats[8];}

namespace {
constexpr uint64_t kGcc = 0x100000, kTlmm = 0xf100000, kSe = 0x9a0000;
constexpr uint32_t kVoteA = 0x52008, kVoteB = 0x52010, kSeCbcr = 0x170e4;
constexpr uint32_t kFw = 0x68, kStatus = 0x40, kClkCfg = 0x48, kClkSel = 0x7c, kCgc = 0x28, kDmaMode = 0x258, kGran = 0x254;
constexpr uint32_t kTxPack0 = 0x260, kTxPack1 = 0x264, kRxPack0 = 0x284, kRxPack1 = 0x288, kTxLen = 0x26c, kRxLen = 0x270, kScl = 0x278;
constexpr uint32_t kCmd = 0x600, kCmdCtrl = 0x604, kIrq = 0x610, kIrqClear = 0x618, kTx = 0x700, kRx = 0x780, kRxStatus = 0x804, kSeIrqEn = 0xe1c, kGsi = 0xe18;
constexpr uint32_t kDone = 1u << 0, kFailure = 1u << 3, kAbort = 1u << 5, kStretch = 1u << 2;
constexpr uint32_t kWrite = 1, kRead = 2;
// Errors only, as i2c-qcom-geni.c reads them: overrun, illegal command, failure, cancel, abort, NACK (GP 1), bus protocol (GP 3), arbitration
// lost (GP 4). V17 counted M_RX_IRQ (bit 7), which every read with data raises, as a failure and threw away every word.
constexpr uint32_t kErrors = 0x3eu | (1u << 10) | (1u << 12) | (1u << 13);
constexpr uint32_t kGauge = 0x71, kCharger = 0x49;
constexpr uint8_t kSramAddress = 0x8c, kSramData = 0x8d, kSoc = 0x00, kCurrent = 0x05, kVbatAvg = 0x08;
constexpr uint8_t kChgStatus1 = 0x0d, kChgStatus2 = 0x0e;
// sec-battery's full-scale point (capacity_max 990 in the owner's driver): 99.0 % of the gauge reads as 100 %.
constexpr uint32_t kCapacityMax = 990, kRefreshMs = 30000;

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
        if (irq & kDone) {reg(kIrqClear) = irq; return !(irq & kErrors);}
        IODelay(10);
    }
    reg(kCmdCtrl) = 1u << 1;
    barrier();
    IODelay(100);
    reg(kIrqClear) = 0xffffffffu;
    return false;
}
// One write of up to 4 bytes (the FIFO word is packed least significant byte first); stretch keeps the bus for a read that follows.
bool i2cWrite(uint32_t address, uint32_t bytes, uint32_t length, bool stretch) {
    reg(kIrqClear) = 0xffffffffu;
    reg(kTxLen) = length;
    reg(kCmd) = (kWrite << 27) | (stretch ? kStretch : 0) | (address << 9);
    barrier();
    reg(kTx) = bytes;
    barrier();
    return finish();
}
bool i2cRead(uint32_t address, uint8_t index, uint32_t length, uint32_t *out) {
    if (!i2cWrite(address, index, 1, true)) return false;
    reg(kRxLen) = length;
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
    *out = length == 1 ? (word & 0xff) : (word & 0xffff);
    return true;
}
bool count(bool ok) {
    if (ok) x910_fuel_stats[3] = x910_fuel_stats[3] + 1;
    else x910_fuel_stats[4] = x910_fuel_stats[4] + 1;
    return ok;
}
bool sram(uint8_t address, uint32_t *out) {
    return count(i2cWrite(kGauge, kSramAddress | (uint32_t)address << 8, 3, false) && i2cRead(kGauge, kSramData, 2, out));
}
int32_t millivolts(uint32_t raw) {return raw & 0x8000 ? 2700 - (int32_t)(((raw & 0x7fff) * 10) / 109) : (int32_t)((raw * 10) / 109) + 2700;}

struct Reading {int32_t percent, mv, ma; bool external, charging, full;};
bool sample(Reading *r) {
    uint32_t soc, vbat, current, st1, st2;
    if (!sram(kSoc, &soc) || !sram(kVbatAvg, &vbat) || !sram(kCurrent, &current)) return false;
    if (!count(i2cRead(kCharger, kChgStatus1, 1, &st1)) || !count(i2cRead(kCharger, kChgStatus2, 1, &st2))) return false;
    const uint32_t permille = (soc * 10) >> 8;
    r->percent = (int32_t)((permille * 100 + kCapacityMax / 2) / kCapacityMax);
    if (r->percent > 100) r->percent = 100;
    r->mv = millivolts(vbat);
    r->ma = (int32_t)(((current & 0x7fff) * 1000) / 2044);
    if (current & 0x8000) r->ma = -r->ma;   // bit 15 marks a discharge
    r->external = st1 & 1;
    r->full = r->external && (st2 & (1u << 5));
    r->charging = r->external && !r->full && (st2 & (1u << 3));
    return true;
}

// IOPMPowerSource's setters are protected, for its subclasses. A class that derives from it only to name them gives member pointers of the
// base, which are then called on the plain instance; nothing is ever made of that class.
struct Setters : IOPMPowerSource {
    static constexpr void (IOPMPowerSource::*installed)(bool) = &Setters::setBatteryInstalled;
    static constexpr void (IOPMPowerSource::*external)(bool) = &Setters::setExternalConnected;
    static constexpr void (IOPMPowerSource::*chargeCapable)(bool) = &Setters::setExternalChargeCapable;
    static constexpr void (IOPMPowerSource::*charging)(bool) = &Setters::setIsCharging;
    static constexpr void (IOPMPowerSource::*critical)(bool) = &Setters::setAtCriticalLevel;
    static constexpr void (IOPMPowerSource::*current)(unsigned int) = &Setters::setCurrentCapacity;
    static constexpr void (IOPMPowerSource::*maximum)(unsigned int) = &Setters::setMaxCapacity;
    static constexpr void (IOPMPowerSource::*voltage)(unsigned int) = &Setters::setVoltage;
    static constexpr void (IOPMPowerSource::*amperage)(int) = &Setters::setAmperage;
    static constexpr void (IOPMPowerSource::*remaining)(int) = &Setters::setTimeRemaining;
    static constexpr void (IOPMPowerSource::*property)(const OSSymbol *, OSObject *) = &Setters::setPSProperty;
};
IOPMPowerSource *gBattery;
bool gPublished;
void publish(const Reading &r) {
    if (!gBattery) {
        gBattery = IOPMPowerSource::powerSource();
        if (!gBattery || !gBattery->attach(IOService::getPlatform())) {
            OSSafeReleaseNULL(gBattery);
            x910_fuel_stats[0] = -5;
            return;
        }
    }
    (gBattery->*Setters::installed)(true);
    (gBattery->*Setters::external)(r.external);
    (gBattery->*Setters::chargeCapable)(r.external);
    (gBattery->*Setters::charging)(r.charging);
    (gBattery->*Setters::critical)(!r.external && r.percent <= 3);
    (gBattery->*Setters::current)((unsigned)r.percent);
    (gBattery->*Setters::maximum)(100);
    (gBattery->*Setters::voltage)((unsigned)(r.mv > 0 ? r.mv : 0));
    (gBattery->*Setters::amperage)(r.ma);
    (gBattery->*Setters::remaining)(0xffff);
    const OSSymbol *key = OSSymbol::withCString("FullyCharged");
    if (key) {(gBattery->*Setters::property)(key, r.full ? kOSBooleanTrue : kOSBooleanFalse); key->release();}
    if (!gPublished) {
        // The properties are in place before anyone finds the service.
        gPublished = true;
        gBattery->registerService();
        x910_fuel_stats[0] = 4;
    }
    gBattery->updateStatus();
}

void fuelThread(void *, wait_result_t) {
    thread_set_thread_name(current_thread(), "x910-battery");
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
    for (unsigned round = 0;; ++round) {
        Reading r{};
        if (sample(&r)) {
            x910_fuel_stats[6] = r.percent;
            x910_fuel_stats[7] = r.mv | (int32_t)((r.external ? 1 : 0) | (r.charging ? 2 : 0) | (r.full ? 4 : 0)) << 16;
            if (round < 4 || !(round % 20)) printf("x910fg %d%% %d mV %d mA ext=%d chg=%d full=%d\n", r.percent, r.mv, r.ma, r.external, r.charging, r.full);
            publish(r);
        }
        IOSleep(kRefreshMs);
    }
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
