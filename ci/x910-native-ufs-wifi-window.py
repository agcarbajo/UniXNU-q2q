"""X910: the WiFi endpoint's DMA window, as sheng's ShengUfs (99d8bcd) sets it up, for sheng-wifi=2 or more.

The WCN7850 on PCIe0 reaches memory through apps_smmu stream 0x1401 and only produces 32-bit addresses, while memory here is above
4 GiB. Its stream gets a context bank of its own, set up at the same moment as the storage stream (sheng found the stream matching
registers unsafe to touch later from another thread), with a stage 1 table that maps 0x10000000 upward onto one contiguous 48 MiB
buffer in 2 MiB blocks. Every page of the window is then translated by the context bank itself to check the table, and the CPU uses
a write-combined second mapping of the buffer, so neither side needs the link to be cache coherent. The storage stream is untouched;
the window is refused when the bank would be the storage's. WifiProbe.cpp reads native_ufs_extra_*; without sheng-wifi>=2 they stay
absent (x910-native-ufs-return.py defines them).

Hardware 2026-11-02 (V30, sheng-wifi=2): PCIe0 link Gen2 x2 in 13 ms, endpoint 17cb:1107 read; the firmware stage stopped at its DMA
buffers (native_wifi_mhi[0] = -3) for want of this window.
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


replace('static uint32_t read32 (uintptr_t base, unsigned offset)\n',
        'extern "C" volatile uint32_t native_ufs_extra_stream, native_ufs_extra_uncached, native_ufs_extra_iova, native_ufs_extra_bytes;\n'
        'extern "C" volatile uint32_t native_ufs_extra_atos[8];\n'
        'extern "C" uint8_t *native_ufs_extra_va;\n'
        'static uintptr_t gWifiSmmuBase, gWifiSmmuContext;\n'
        'static uint32_t read32 (uintptr_t base, unsigned offset)\n')
replace('int native_ufs_extra_fault (uint32_t *) { return 0; }', r'''int native_ufs_extra_fault (uint32_t *out)
{
  if (!out || !gWifiSmmuBase || !gWifiSmmuContext) return 0;
  out[0] = read32 (gWifiSmmuBase, 0x48);
  out[1] = read32 (gWifiSmmuBase, 0x24);
  out[2] = read32 (gWifiSmmuContext, 0x58);
  out[3] = read32 (gWifiSmmuContext, 0x60);
  out[4] = read32 (gWifiSmmuContext, 0x64);
  out[5] = read32 (gWifiSmmuContext, 0x68);
  out[6] = read32 (gWifiSmmuContext, 0x0);
  out[7] = read32 (gWifiSmmuContext, 0x30);
  out[8] = read32 (gWifiSmmuContext, 0x20);
  out[9] = read32 (gWifiSmmuContext, 0x24);
  return 1;
}''')
replace('''  smmuBypassStream (0x60, gHost.smmuMap, report, gHost.phys, kDmaBytes);
  gHost.smmu = gHost.smmuMap->getVirtualAddress ();
''', r'''  smmuBypassStream (0x60, gHost.smmuMap, report, gHost.phys, kDmaBytes);
  gHost.smmu = gHost.smmuMap->getVirtualAddress ();
  uint32_t wifi = 0;
  if (PE_parse_boot_argn ("sheng-wifi", &wifi, sizeof (wifi)) && wifi >= 2) {
    static OSSharedPtr<IOMemoryMap> extraMap;
    uint32_t extraReport[3] = { 0, 0, 0 };
    smmuBypassStream (0x1401, extraMap, extraReport, 0x9c900000ULL, 8ULL << 20);
    constexpr uint32_t kExtraIova = 0x10000000U, kExtraBlocks = 24, kBlock = 2U << 20;
    IOBufferMemoryDescriptor *buffer = IOBufferMemoryDescriptor::inTaskWithOptions (
      kernel_task, static_cast<IOOptionBits> (kIODirectionInOut) | static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
      static_cast<IOOptionBits> (kIOMemoryMapperNone), (kExtraBlocks + 1) * kBlock, kBlock);
    IOBufferMemoryDescriptor *tables = IOBufferMemoryDescriptor::inTaskWithOptions (
      kernel_task, static_cast<IOOptionBits> (kIODirectionInOut) | static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
      static_cast<IOOptionBits> (kIOMemoryMapperNone), 8192, 4096);
    if (buffer && tables && extraMap && (extraReport[0] >> 16) != (report[0] >> 16)) {
      uint8_t *bva = static_cast<uint8_t *> (buffer->getBytesNoCopy ());
      uint64_t bpa = buffer->getPhysicalSegment (0, nullptr, kIOMemoryMapperNone);
      uint64_t shift = (kBlock - (bpa & (kBlock - 1))) & (kBlock - 1);
      bva += shift; bpa += shift;
      bzero (buffer->getBytesNoCopy (), (kExtraBlocks + 1) * kBlock);
      uint64_t *l1 = static_cast<uint64_t *> (tables->getBytesNoCopy ());
      uint64_t *l2 = l1 + 512;
      bzero (l1, 8192);
      const uint64_t tablesPa = tables->getPhysicalSegment (0, nullptr, kIOMemoryMapperNone);
      l1[0] = (tablesPa + 4096) | 3;
      for (uint32_t i = 0; i < kExtraBlocks; ++i)
        l2[(kExtraIova >> 21) + i] = (bpa + i * kBlock) | (1ULL << 11) | (1ULL << 10) | (3ULL << 8) | (1ULL << 6) | 1;
      for (uint32_t off = 0; off < 8192; off += 64) __asm__ volatile ("dc civac, %0" :: "r" (reinterpret_cast<uint8_t *> (l1) + off) : "memory");
      __asm__ volatile ("dsb sy" ::: "memory");
      uintptr_t smmuBase = extraMap->getVirtualAddress ();
      unsigned pages = 1U << (((read32 (smmuBase, 0x24) >> 28) & 7) + 1);
      // smmuBypassStream maps the stream to the last context bank, the bypass bank every pass-through stream of this board shares
      // (SmmuBypass.h, the touch DMA's included). V31 turned that bank to translation and the touch DMA refused it. The window's stream
      // moves to a bank of its own: the highest one below it that no valid stream-match group points at and whose translation is off.
      const unsigned banks = read32 (smmuBase, 0x24) & 255, groups = read32 (smmuBase, 0x0) & 255;
      unsigned own = banks;
      for (unsigned candidate = banks - 1; candidate-- > 0 && own == banks;) {
        bool used = (read32 (smmuBase + (pages + candidate) * 4096, 0x0) & 1) != 0;
        for (unsigned g = 0; g < groups && !used; g++) {
          if ((read32 (smmuBase, 0x800 + 4 * g) & 0x80000000U) && (read32 (smmuBase, 0xc00 + 4 * g) & 255) == candidate) used = true;
        }
        if (!used) own = candidate;
      }
      native_ufs_extra_atos[2] = own | (banks << 8) | (groups << 16);
      if (own != banks) {
      write32 (smmuBase, 0x1800 + 4 * own, 1);           // CBA2R: AArch64 tables
      write32 (smmuBase, 0x1000 + 4 * own, 0x10000);     // CBAR: stage 1 translation, stage 2 bypass, as the bypass bank's type
      write32 (smmuBase, 0xc00 + 4 * (extraReport[0] & 0xffff), own);
      __asm__ volatile ("dsb sy" ::: "memory");
      extraReport[0] = (extraReport[0] & 0xffff) | (own << 16);
      uintptr_t cb = smmuBase + (pages + own) * 4096;
      gWifiSmmuBase = smmuBase; gWifiSmmuContext = cb;
      write32 (cb, 0x0, 0);
      write32 (cb, 0x20, static_cast<uint32_t> (tablesPa)); write32 (cb, 0x24, static_cast<uint32_t> (tablesPa >> 32));
      write32 (cb, 0x10, 5);
      write32 (cb, 0x30, 32U | (1U << 23));
      write32 (cb, 0x38, 0xff);
      write32 (cb, 0x618, 0); write32 (cb, 0x7f0, 0);
      __asm__ volatile ("dsb sy" ::: "memory");
      write32 (cb, 0x0, 1U | (1U << 5) | (1U << 6));
      __asm__ volatile ("dsb sy; isb" ::: "memory");
      uint32_t tested = 0, faults = 0, wrong = 0, first = 0;
      for (uint64_t iova = kExtraIova; iova < kExtraIova + static_cast<uint64_t> (kExtraBlocks) * kBlock; iova += 4096) {
        write32 (cb, 0x800, static_cast<uint32_t> (iova)); write32 (cb, 0x804, 0);
        uint32_t spin = 0;
        while ((read32 (cb, 0x8f0) & 1) && ++spin < 100000) { }
        if (spin >= 100000) { native_ufs_extra_atos[3] = 0xdead0000U | tested; break; }
        const uint32_t parLo = read32 (cb, 0x50), parHi = read32 (cb, 0x54);
        ++tested;
        if (tested == 1) { native_ufs_extra_atos[4] = parLo; native_ufs_extra_atos[5] = parHi; native_ufs_extra_atos[6] = static_cast<uint32_t> (bpa); native_ufs_extra_atos[7] = static_cast<uint32_t> (bpa >> 32); }
        if (parLo & 1) { if (!faults++) first = static_cast<uint32_t> (iova); continue; }
        const uint64_t pa = ((static_cast<uint64_t> (parHi) << 32) | parLo) & 0x0000fffffffff000ULL;
        if (pa != ((bpa + (iova - kExtraIova)) & ~0xfffULL)) { if (!wrong++ && !first) first = static_cast<uint32_t> (iova); }
      }
      native_ufs_extra_atos[0] = tested; native_ufs_extra_atos[1] = faults; native_ufs_extra_atos[2] = wrong;
      if (native_ufs_extra_atos[3] == 0) native_ufs_extra_atos[3] = first;
      for (uint64_t off = 0; off < (kExtraBlocks + 1) * static_cast<uint64_t> (kBlock); off += 64)
        __asm__ volatile ("dc civac, %0" :: "r" (static_cast<uint8_t *> (buffer->getBytesNoCopy ()) + off) : "memory");
      __asm__ volatile ("dsb sy" ::: "memory");
      IOMemoryMap *wc = buffer->createMappingInTask (kernel_task, 0, kIOMapAnywhere | kIOMapWriteCombineCache);
      native_ufs_extra_va = wc ? reinterpret_cast<uint8_t *> (wc->getVirtualAddress ()) + (bva - static_cast<uint8_t *> (buffer->getBytesNoCopy ())) : bva;
      native_ufs_extra_uncached = wc != nullptr;
      native_ufs_extra_bytes = kExtraBlocks * kBlock;
      native_ufs_extra_iova = kExtraIova;
      }
    }
    native_ufs_extra_stream = 0x80000000U | extraReport[0];
  }
''')
source.write_text(text)
print('X910 WiFi DMA window added to', source)
