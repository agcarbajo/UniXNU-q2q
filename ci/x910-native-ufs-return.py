"""X910: x910-return-twrp=<seconds>: after that time, ask the bootloader for recovery and reset, for unattended test rounds.

Hardware 2026-11-02: from TWRP, "boot-recovery" in the bootloader message at the start of the misc partition made a plain
reboot land in TWRP, which cleared the message itself. So the driver writes one block: misc, found by name in the checked GPT,
of exactly 1 MiB, and only if its first 2048 bytes are zero as measured. Everything else in that block is written back as read.
Status (x910_return_twrp_status): 0 off, 1 waiting, 2 GPT, 3 misc not as measured, 4 write, 5 read back; a failure stays in iPadOS.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Pinned NativeUfs context changed: ' + old[:50])
    text = text.replace(old, new)


replace('extern "C" int native_ufs_store_read (uint64_t rel, uint32_t blocks, void *dst)\n', r'''volatile int x910_return_twrp_status = 0;
static uint64_t x910Le64 (const uint8_t *at)
{
  uint64_t value;
  memcpy (&value, at, sizeof (value));
  return value;
}
static void x910ReturnThread (void *argument, wait_result_t)
{
  x910_return_twrp_status = 1;
  IOSleep (static_cast<uint32_t> (reinterpret_cast<uintptr_t> (argument)) * 1000U);
  enum { Blocks = 4 };
  uint8_t *block = static_cast<uint8_t *> (IOMallocData (Blocks * 4096U + 4096U));
  x910_return_twrp_status = 2;
  if (!block || hostRead4k (0, 1, 1, block) != kIOReturnSuccess || memcmp (block, "EFI PART", 8)) return;
  const uint64_t entries = x910Le64 (block + 72);
  uint32_t count, size;
  memcpy (&count, block + 80, 4);
  memcpy (&size, block + 84, 4);
  if (entries < 2 || entries > 64 || !count || count > 128 || size != 128 || hostRead4k (0, entries, Blocks, block) != kIOReturnSuccess) return;
  static const char wanted[] = "misc";
  uint64_t first = 0, last = 0;
  for (uint32_t index = 0; index < count && !first; index++) {
    const uint8_t *entry = block + index * 128;
    bool match = true;
    for (unsigned c = 0; c < sizeof (wanted); c++) {
      if (entry[56 + 2 * c] != static_cast<uint8_t> (wanted[c]) || entry[57 + 2 * c] != 0) match = false;
    }
    if (match) {
      first = x910Le64 (entry + 32);
      last = x910Le64 (entry + 40);
    }
  }
  x910_return_twrp_status = 3;
  if (!first || last < first || last - first != 255 || first > 0xffffffffULL) return;
  if (hostRead4k (0, first, 1, block) != kIOReturnSuccess) return;
  for (unsigned i = 0; i < 2048; i++) {
    if (block[i]) return;
  }
  memcpy (block, "boot-recovery", 13);
  memcpy (block + 64, "recovery\n", 9);
  x910_return_twrp_status = 4;
  IOLockLock (gHost.lock);
  memcpy (gHost.virt + kDataOffset, block, 4096U);
  uint8_t cdb[10] = { 0x2a, 0x08, uint8_t (first >> 24), uint8_t (first >> 16), uint8_t (first >> 8), uint8_t (first), 0, 0, 1, 0 };
  uint32_t scsi = 0;
  IOReturn status = hostScsiDir (0, cdb, 10, 1, 4096U, 2000000, &scsi);
  IOLockUnlock (gHost.lock);
  if (status != kIOReturnSuccess) return;
  x910_return_twrp_status = 5;
  uint8_t *check = block + Blocks * 4096U;
  if (hostRead4k (0, first, 1, check) != kIOReturnSuccess || memcmp (check, block, 4096U)) return;
  (void) hostSyncCache ();
  IOSleep (200);
  // PSCI SYSTEM_RESET, as ArmDTPlatform's stage reset.
  register uint64_t x0 __asm__ ("x0") = 0x84000009;
  register uint64_t x1 __asm__ ("x1") = 0;
  register uint64_t x2 __asm__ ("x2") = 0;
  register uint64_t x3 __asm__ ("x3") = 0;
  __asm__ volatile ("smc #0" : "+r" (x0) : "r" (x1), "r" (x2), "r" (x3) : "memory");
  for (;;) { __asm__ volatile ("wfe"); }
}
static void x910ReturnStart (void)
{
  uint32_t seconds = 0;
  if (!PE_parse_boot_argn ("x910-return-twrp", &seconds, sizeof (seconds)) || seconds < 60 || seconds > 3600) return;
  thread_t thread = nullptr;
  if (kernel_thread_start (x910ReturnThread, reinterpret_cast<void *> (static_cast<uintptr_t> (seconds)), &thread) == KERN_SUCCESS) {
    thread_deallocate (thread);
  }
}

extern "C" int native_ufs_store_read (uint64_t rel, uint32_t blocks, void *dst)
''')
replace('      thread_t thread = nullptr;\n      owner->retain ();\n      if (kernel_thread_start (publishThread, nullptr, &thread) != KERN_SUCCESS) {\n',
        '      x910ReturnStart ();\n      thread_t thread = nullptr;\n      owner->retain ();\n      if (kernel_thread_start (publishThread, nullptr, &thread) != KERN_SUCCESS) {\n')
source.write_text(text)
print('X910 return to recovery added to', source)
