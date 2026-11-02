"""X910: x910-return-twrp=<seconds>: after that time, ask the bootloader for recovery and reset, for unattended test rounds.

Hardware 2026-11-02: from TWRP, "boot-recovery" in the bootloader message at the start of the misc partition made a plain
reboot land in TWRP, which cleared the message itself. So the driver writes one block: misc, found by name in the checked GPT,
of exactly 1 MiB, and only if its first 2048 bytes are zero as measured; written first thing, so any later reset lands in TWRP. A panic resets too.
Status (x910_return_twrp_status): 0 off, 1 armed and waiting, 2 GPT, 3 misc not as measured, 4 write, 5 read back, 6 resetting; a failure still resets at the timer.
A thread cannot reset a hung kernel (V14 stayed on its panic screen): with misc armed, the hypervisor's watchdog is armed too, as upstream
sheng d637499 does (Gunyah SMCCC 0x86000005 control, 6 status, 7 ping, 8 set time, Linux gunyah_wdt), 60 s, pinged every 10 s until
30 s past the timer; a hang or a stopped kernel then restarts in at most 60 s and lands in TWRP. x910_return_wdt: status, set time, enable, pings.
The panic screen calls native_ufs_panic_note after its 30 s (tools/prepare_source_xnu_test.py), so the text reaches misc block 128 with the
caller chain and the mounts still listed; the watchdog stops being pinged as the note starts, so a note that hangs is bitten in 60 s.
x910-return-twrp=302 restarts through reboot_kernel at 3 minutes, which unmounts everything as a shutdown does; the mounts listed just
before go to the note too, and the one missing from the panic's list (busy being unmounted) is the one that failed.
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


replace('extern "C" int native_ufs_store_read (uint64_t rel, uint32_t blocks, void *dst)\n', r'''#include <sys/mount.h>
extern "C" int reboot_kernel (int, char *);
volatile int x910_return_twrp_status = 0;
static uint64_t x910Le64 (const uint8_t *at)
{
  uint64_t value;
  memcpy (&value, at, sizeof (value));
  return value;
}
static uint64_t gReturnPanicLba = 0;
static volatile bool gReturnPanicking = false;
struct X910Text {char *text; size_t size; int used;};
static int x910MountName (mount_t mp, void *argument)
{
  X910Text *out = static_cast<X910Text *> (argument);
  struct vfsstatfs *stat = vfs_statfs (mp);
  if (stat && out->used >= 0 && out->used < (int) out->size - 2)
    out->used += snprintf (out->text + out->used, out->size - out->used, " %s(%s)", stat->f_mntonname, stat->f_fstypename);
  return VFS_RETURNED;
}
static char gReturnMountsBefore[1024];
struct X910Fsids {fsid_t fsid[8]; unsigned count;};
static int x910BindFsid (mount_t mp, void *argument)
{
  X910Fsids *out = static_cast<X910Fsids *> (argument);
  struct vfsstatfs *stat = vfs_statfs (mp);
  if (stat && out->count < 8 && !strncmp (stat->f_fstypename, "bindfs", sizeof (stat->f_fstypename))) out->fsid[out->count++] = stat->f_fsid;
  return VFS_RETURNED;
}
volatile int x910_return_wdt[4] = { -99, -99, -99, 0 };
static uint64_t x910Smc (uint64_t fid, uint64_t a1, uint64_t a2)
{
  register uint64_t x0 __asm__ ("x0") = fid, x1 __asm__ ("x1") = a1, x2 __asm__ ("x2") = a2, x3 __asm__ ("x3") = 0;
  __asm__ volatile ("smc #0" : "+r" (x0), "+r" (x1), "+r" (x2), "+r" (x3) :: "memory", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
                    "x12", "x13", "x14", "x15", "x16", "x17");
  return x0;
}
static void x910WatchdogThread (void *argument, wait_result_t)
{
  const uint64_t pings = reinterpret_cast<uintptr_t> (argument) / 10U + 3U;
  for (uint64_t i = 0; i < pings && !gReturnPanicking; i++) {
    (void) x910Smc (0x86000007, 0, 0);
    x910_return_wdt[3] = x910_return_wdt[3] + 1;
    IOSleep (10000);
  }
}
static void x910WatchdogStart (uint32_t seconds)
{
  x910_return_wdt[0] = static_cast<int> (x910Smc (0x86000006, 0, 0));
  if (x910_return_wdt[0] != 0) return;
  (void) x910Smc (0x86000005, 2, 0);
  x910_return_wdt[1] = static_cast<int> (x910Smc (0x86000008, 60000, 60000));
  if (x910_return_wdt[1] != 0) return;
  thread_t thread = nullptr;
  if (kernel_thread_start (x910WatchdogThread, reinterpret_cast<void *> (static_cast<uintptr_t> (seconds)), &thread) != KERN_SUCCESS) return;
  thread_deallocate (thread);
  x910_return_wdt[2] = static_cast<int> (x910Smc (0x86000005, 3, 0));
}
static bool gReturnArmed = false;
static void x910ReturnReset (void)
{
  // PSCI SYSTEM_RESET, as ArmDTPlatform's stage reset.
  register uint64_t x0 __asm__ ("x0") = 0x84000009;
  register uint64_t x1 __asm__ ("x1") = 0;
  register uint64_t x2 __asm__ ("x2") = 0;
  register uint64_t x3 __asm__ ("x3") = 0;
  __asm__ volatile ("smc #0" : "+r" (x0) : "r" (x1), "r" (x2), "r" (x3) : "memory");
  for (;;) { __asm__ volatile ("wfe"); }
}
static IOReturn x910ReturnWrite (uint64_t lba, const void *data)
{
  memcpy (gHost.virt + kDataOffset, data, 4096U);
  uint8_t cdb[10] = { 0x2a, 0x08, uint8_t (lba >> 24), uint8_t (lba >> 16), uint8_t (lba >> 8), uint8_t (lba), 0, 0, 1, 0 };
  uint32_t scsi = 0;
  return hostScsiDir (0, cdb, 10, 1, 4096U, 2000000, &scsi);
}
// A panic with the return armed: its text in misc block 128 for TWRP to read, then the same reset. misc already asks for recovery.
extern "C" void native_ufs_panic_note (const char *format, va_list *arguments)
{
  static volatile uint32_t entered = 0;
  if (!gReturnArmed) return;
  gReturnPanicking = true;
  if (__atomic_exchange_n (&entered, 1, __ATOMIC_ACQ_REL)) x910ReturnReset ();
  static char text[4096];
  int used = snprintf (text, sizeof (text), "X910PANIC ");
  if (format && arguments && used > 0) {
    va_list copy;
    va_copy (copy, *arguments);
    vsnprintf (text + used, sizeof (text) - used, format, copy);
    va_end (copy);
  }
  used = (int) strnlen (text, sizeof (text));
  used += snprintf (text + used, sizeof (text) - used, " bt");
  const uint64_t *fp = static_cast<const uint64_t *> (__builtin_frame_address (0));
  for (int i = 0; i < 16 && fp && !(reinterpret_cast<uintptr_t> (fp) & 15) && (reinterpret_cast<uintptr_t> (fp) >> 48) == 0xffff &&
       used < (int) sizeof (text) - 24; i++) {
    used += snprintf (text + used, sizeof (text) - used, " %llx", (unsigned long long) fp[1]);
    fp = reinterpret_cast<const uint64_t *> (fp[0]);
  }
  if (gReturnMountsBefore[0] && used < (int) sizeof (text) - 16)
    used += snprintf (text + used, sizeof (text) - used, " before:%s", gReturnMountsBefore);
  if (used < (int) sizeof (text) - 16) {
    used += snprintf (text + used, sizeof (text) - used, " now:");
    X910Text out = { text, sizeof (text) - 1, used };
    (void) vfs_iterate (VFS_ITERATE_TAIL_FIRST, x910MountName, &out);
  }
  // The panic screen does not stop the other CPUs, so one of them may be inside a UFS command with the lock held: wait up to 2 s for it.
  // One try was not enough (V25: the note ran and reset, misc stayed empty).
  bool locked = false;
  for (unsigned i = 0; i < 2000 && gReturnPanicLba && !(locked = IOLockTryLock (gHost.lock)); i++) IODelay (1000);
  if (locked) {
    (void) x910ReturnWrite (gReturnPanicLba, text);
    uint8_t cdb[10] = { 0x35 };
    uint32_t scsi = 0;
    (void) hostScsiDir (0, cdb, 10, 0, 0, 1000000, &scsi);
    IOLockUnlock (gHost.lock);
  }
  x910ReturnReset ();
}
// Called by reboot_kernel once every unmount has finished (x910-shutdown-mark.patch): a normal context, so the write is the plain one.
extern "C" void native_ufs_shutdown_mark (const char *text)
{
  if (!gReturnArmed || !gReturnPanicLba || !text) return;
  static char block[4096];
  snprintf (block, sizeof (block), "%s mounts:%s", text, gReturnMountsBefore);
  IOLockLock (gHost.lock);
  (void) x910ReturnWrite (gReturnPanicLba, block);
  IOLockUnlock (gHost.lock);
  (void) hostSyncCache ();
}
static void x910ReturnThread (void *argument, wait_result_t)
{
  // x910-return-twrp=301 tests the panic path: a panic at 3 minutes instead of the timed reset.
  if (reinterpret_cast<uintptr_t> (argument) == 301) {
    IOSleep (180000);
    panic ("X910 return test");
  }
  // 303: the same, but the boot's bindfs overlays are unmounted first, one by one, to see whether they are what breaks the shutdown.
  if (reinterpret_cast<uintptr_t> (argument) == 302 || reinterpret_cast<uintptr_t> (argument) == 303) {
    IOSleep (180000);
    char bind[96] = "";
    if (reinterpret_cast<uintptr_t> (argument) == 303) {
      X910Fsids found = {};
      (void) vfs_iterate (VFS_ITERATE_TAIL_FIRST, x910BindFsid, &found);
      int used = 0;
      for (unsigned i = 0; i < found.count && used < (int) sizeof (bind) - 8; i++)
        used += snprintf (bind + used, sizeof (bind) - used, " %d", vfs_unmountbyfsid (&found.fsid[i], 0, vfs_context_current ()));
    }
    X910Text out = { gReturnMountsBefore, sizeof (gReturnMountsBefore) - 1, 0 };
    (void) vfs_iterate (VFS_ITERATE_TAIL_FIRST, x910MountName, &out);
    printf ("x910 return through reboot_kernel; bindfs unmounts:%s mounts:%s\n", bind, gReturnMountsBefore);
    // A mark in the note's block: still there after the restart means no panic note was written (no panic, or a note that failed).
    if (gReturnPanicLba) {
      static char mark[4096];
      snprintf (mark, sizeof (mark), "X910REBOOT bindfs:%s mounts:%s", bind, gReturnMountsBefore);
      IOLockLock (gHost.lock);
      (void) x910ReturnWrite (gReturnPanicLba, mark);
      IOLockUnlock (gHost.lock);
      (void) hostSyncCache ();
    }
    IOSleep (2000);
    (void) reboot_kernel (0, const_cast<char *> ("x910 return test"));
  }
  IOSleep (static_cast<uint32_t> (reinterpret_cast<uintptr_t> (argument)) * 1000U);
  x910_return_twrp_status = 6;
  (void) hostSyncCache ();
  IOSleep (200);
  x910ReturnReset ();
}
// Written before the root is published, so that from here on any reset lands in TWRP, whatever the system does next.
static bool x910ReturnPrepare (void)
{
  enum { Blocks = 4 };
  uint8_t *block = static_cast<uint8_t *> (IOMallocData (Blocks * 4096U + 4096U));
  x910_return_twrp_status = 2;
  if (!block || hostRead4k (0, 1, 1, block) != kIOReturnSuccess || memcmp (block, "EFI PART", 8)) return false;
  const uint64_t entries = x910Le64 (block + 72);
  uint32_t count, size;
  memcpy (&count, block + 80, 4);
  memcpy (&size, block + 84, 4);
  if (entries < 2 || entries > 64 || !count || count > 128 || size != 128 || hostRead4k (0, entries, Blocks, block) != kIOReturnSuccess) return false;
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
  if (!first || last < first || last - first != 255 || first > 0xffffffffULL) return false;
  if (hostRead4k (0, first, 1, block) != kIOReturnSuccess) return false;
  for (unsigned i = 0; i < 2048; i++) {
    if (block[i]) return false;
  }
  memcpy (block, "boot-recovery", 13);
  memcpy (block + 64, "recovery\n", 9);
  x910_return_twrp_status = 4;
  IOLockLock (gHost.lock);
  IOReturn status = x910ReturnWrite (first, block);
  IOLockUnlock (gHost.lock);
  if (status != kIOReturnSuccess) return false;
  x910_return_twrp_status = 5;
  uint8_t *check = block + Blocks * 4096U;
  if (hostRead4k (0, first, 1, check) != kIOReturnSuccess || memcmp (check, block, 4096U)) return false;
  // The panic note's block: only one that is empty or holds an earlier note.
  if (hostRead4k (0, first + 128, 1, check) == kIOReturnSuccess) {
    bool empty = !memcmp (check, "X910PANIC", 9);
    if (!empty) {
      empty = true;
      for (unsigned i = 0; i < 4096; i++) {
        if (check[i]) empty = false;
      }
    }
    if (empty) gReturnPanicLba = first + 128;
  }
  gReturnArmed = true;
  x910_return_twrp_status = 1;
  return true;
}
static void x910ReturnStart (void)
{
  uint32_t seconds = 0;
  if (!PE_parse_boot_argn ("x910-return-twrp", &seconds, sizeof (seconds)) || seconds < 60 || seconds > 3600) return;
  static bool started = false;
  if (started) return;
  started = true;
  if (x910ReturnPrepare ()) x910WatchdogStart (seconds);
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
