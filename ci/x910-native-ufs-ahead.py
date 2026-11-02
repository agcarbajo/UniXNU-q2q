"""X910: x910-ufs-ahead=1: root reads fetch the whole aligned 128 KiB around a small request and keep the last 32 such runs.

Hardware 2026-11-02 (V8): 470228 root reads of 2.8 blocks on average took 41.5 s in 5 minutes, one command at a time. Only the
bytes change hands here: the window reader still verifies every block it returns against the owner manifest on every use, hit or
miss, and the root is read-only. A run the device refuses, and a request crossing two runs, read exactly what was asked.
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


replace('extern "C" int native_ufs_store_read (uint64_t rel, uint32_t blocks, void *dst)\n', r'''volatile uint64_t x910_ahead_hits = 0, x910_ahead_misses = 0;
static IOLock *gAheadLock = nullptr;
static uint8_t *gAheadData = nullptr;
enum { kAheadBlocks = 32, kAheadRuns = 32 };
static uint64_t gAheadTag[kAheadRuns];   // first LBA + 1; 0 is empty
static unsigned gAheadNext = 0;
static void x910AheadInit (void)
{
  uint32_t enabled = 0;
  if (gAheadLock || !PE_parse_boot_argn ("x910-ufs-ahead", &enabled, sizeof (enabled)) || enabled != 1) return;
  uint8_t *data = static_cast<uint8_t *> (IOMallocData (kAheadRuns * kAheadBlocks * 4096U));
  IOLock *lock = data ? IOLockAlloc () : nullptr;
  if (!lock) {
    if (data) IOFreeData (data, kAheadRuns * kAheadBlocks * 4096U);
    return;
  }
  gAheadData = data;
  gAheadLock = lock;
}
static IOReturn x910AheadRead (uint64_t lba, uint32_t blocks, void *out)
{
  const uint64_t first = lba & ~uint64_t (kAheadBlocks - 1);
  if (!gAheadLock || lba + blocks > first + kAheadBlocks) return hostRead4k (0, lba, blocks, out);
  IOLockLock (gAheadLock);
  unsigned run = 0;
  while (run < kAheadRuns && gAheadTag[run] != first + 1) run++;
  IOReturn status = kIOReturnSuccess;
  if (run == kAheadRuns) {
    run = gAheadNext;
    gAheadNext = (gAheadNext + 1) % kAheadRuns;
    gAheadTag[run] = 0;
    status = hostRead4k (0, first, kAheadBlocks, gAheadData + run * kAheadBlocks * 4096U);
    if (status == kIOReturnSuccess) gAheadTag[run] = first + 1;
    __atomic_fetch_add (&x910_ahead_misses, 1, __ATOMIC_RELAXED);
  } else {
    __atomic_fetch_add (&x910_ahead_hits, 1, __ATOMIC_RELAXED);
  }
  if (status == kIOReturnSuccess) memcpy (out, gAheadData + run * kAheadBlocks * 4096U + (lba - first) * 4096U, blocks * 4096U);
  IOLockUnlock (gAheadLock);
  return status == kIOReturnSuccess ? status : hostRead4k (0, lba, blocks, out);
}

extern "C" int native_ufs_store_read (uint64_t rel, uint32_t blocks, void *dst)
''')
replace('      IOReturn r = hostRead4k (0, lba, blocks, out);\n', '      IOReturn r = x910AheadRead (lba, blocks, out);\n')
replace('  if (!bounce || !windowLock || !IOBlockStorageDevice::start (provider)) {\n',
        '  x910AheadInit ();\n  if (!bounce || !windowLock || !IOBlockStorageDevice::start (provider)) {\n')
source.write_text(text)
print('X910 root read-ahead added to', source)
