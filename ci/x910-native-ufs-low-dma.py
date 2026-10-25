"""Use a loader-reserved DMA block below 4 GiB for the X910 copy of NativeUfs's request lists and descriptors.

Hardware 2026-10-25: with the link active (TX_FSM 5, PA_PWRMode 0x11, 2+2 lanes), the controller ready and the SMMU
stream adopted without new faults, NativeUfs's first NOP OUT never completes and nothing reports an error. XNU's only
RAM on the X910 is DarwinBoot's 512 MiB window above 4 GiB (0x88ba7c000); the request list was at 0x894a69000. The
UEFI's working UFS DMA used lists at 0x0bda63000. No 512 MiB run exists below 4 GiB for the whole window.

DarwinBoot (X910 UFS-DMA variant) reserves 256 KiB below 4 GiB as EfiLoaderData and publishes it in
/chosen/memory-map/X910UfsDma = {physical address, bytes}. When that property is present and valid, NativeUfs maps
it write-back cacheable (its existing clean/invalidate maintenance then applies unchanged) instead of allocating from
the high window. Without the property, sheng's allocation is used as before.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
old = '''  gRootDiagnostic.stage = "DMA allocation";
  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions (
    kernel_task, static_cast<IOOptionBits> (kIODirectionInOut) | static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
    static_cast<IOOptionBits> (kIOMemoryMapperNone),
    kDmaBytes, 4096);
  gHost.lock = IOLockAlloc ();
  if (!gHost.dma || !gHost.lock) {
    *why = "dma buffer";
    return false;
  }
  gHost.virt = static_cast<uint8_t *> (gHost.dma->getBytesNoCopy ());
  IOByteCount contiguous = 0;
  gHost.phys = gHost.dma->getPhysicalSegment (0, &contiguous, kIOMemoryMapperNone);
'''
new = '''  gRootDiagnostic.stage = "DMA allocation";
  // X910: prefer the loader's reserved block below 4 GiB (/chosen/memory-map/X910UfsDma).
  uint64_t lowDma = 0, lowBytes = 0;
  if (IORegistryEntry *map = IORegistryEntry::fromPath ("/chosen/memory-map", gIODTPlane)) {
    OSData *range = OSDynamicCast (OSData, map->getProperty ("X910UfsDma"));
    if (range && range->getLength () == 16) {
      memcpy (&lowDma, range->getBytesNoCopy (), 8);
      memcpy (&lowBytes, static_cast<const uint8_t *> (range->getBytesNoCopy ()) + 8, 8);
    }
    map->release ();
  }
  gHost.lock = IOLockAlloc ();
  IOByteCount contiguous = 0;
  if (lowDma) {
    static OSSharedPtr<IOMemoryMap> lowMap;
    if ((lowDma & 4095) || lowBytes < kDmaBytes || lowDma + kDmaBytes > 0x100000000ULL || !gHost.lock) {
      *why = "X910 low DMA block invalid";
      return false;
    }
    IOMemoryDescriptor *low = IOMemoryDescriptor::withPhysicalAddress (lowDma, kDmaBytes, kIODirectionInOut);
    if (low) {
      lowMap = OSSharedPtr<IOMemoryMap> (low->map (kIOMapAnywhere | kIOMapCopybackCache), OSNoRetain);
      low->release ();
    }
    if (!lowMap) {
      *why = "X910 low DMA map";
      return false;
    }
    gHost.virt = reinterpret_cast<uint8_t *> (lowMap->getVirtualAddress ());
    gHost.phys = lowDma;
    contiguous = kDmaBytes;
  } else {
  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions (
    kernel_task, static_cast<IOOptionBits> (kIODirectionInOut) | static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
    static_cast<IOOptionBits> (kIOMemoryMapperNone),
    kDmaBytes, 4096);
  if (!gHost.dma || !gHost.lock) {
    *why = "dma buffer";
    return false;
  }
  gHost.virt = static_cast<uint8_t *> (gHost.dma->getBytesNoCopy ());
  gHost.phys = gHost.dma->getPhysicalSegment (0, &contiguous, kIOMemoryMapperNone);
  }
'''
if text.count(old) != 1:
    raise SystemExit('Pinned NativeUfs DMA allocation context changed')
source.write_text(text.replace(old, new))
print('X910 low DMA block support added to', source)
