"""Patch the X910 copy of NativeUfs's SMMU planner to adopt the Samsung/Qualcomm firmware's own UFS mapping.

On the Xiaomi Pad 6S Pro the firmware leaves no stream-match entry for the UFS host (stream 0x60), so sheng allocates
the top context bank as a stage-1-disabled bypass, as Linux's qcom "bypass_quirk" does. On the SM-X910 the firmware
already maps stream 0x60 exclusively (observed: SMR8=0x80000060, S2CR8=8 -> context bank 8, SCTLR.M=0, TTBR0=0), and
the UEFI's own UFS DMA uses it; sheng's planner reports "conflicting UFS stream mapping" for that.

Added path, tried only after sheng's own planning refused with that conflict: exactly one valid, unmasked SMR matches
the stream; its S2CR is TYPE=TRANSLATE to an implemented bank; no other valid SMR uses that bank; the bank's
SCTLR.M is 0 (no translation); and the DMA range fits the bank's input width (VA64 set, or below 4 GiB). Then the
mapping is adopted unchanged and configuration performs no SMMU write at all, only readbacks. Anything else keeps
sheng's refusal.
"""
import sys
from pathlib import Path

header = Path(sys.argv[1])
text = header.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Pinned SmmuTakeover.h context changed: ' + old[:60])
    text = text.replace(old, new)


replace('  bool reuse = false;\n', '  bool reuse = false;\n  bool firmware = false;   // X910: adopt the firmware\'s exclusive stage-1-disabled mapping, no writes\n')
replace('''// The UFS stream's context bank: the top bank first.''', '''// X910: the firmware's own exclusive mapping of the stream, when it is already a pass-through.
template <class Read>
static UfsSmmuPlan planFirmwareUfsBypass(uint16_t stream, uint64_t dma, uint64_t bytes, Read read)
{
  UfsSmmuPlan p;
  uint32_t idr0 = read(0x20), idr1 = read(0x24);
  unsigned count = idr0 & 255, banks = idr1 & 255;
  p.pages = 1U << (((idr1 >> 28) & 7) + 1);
  if (count > 128) count = 128;
  if (!count || banks < 2 || banks > 128 || p.pages > 128 || (p.pages + banks) * 4096 > 0xd0000) {
    p.error = "unsupported SMMU geometry"; return p;
  }
  unsigned matches = 0;
  for (unsigned i = 0; i < count; ++i) {
    uint32_t smr = read(0x800 + 4 * i);
    if (!(smr & 0x80000000U)) continue;
    uint32_t mask = (smr >> 16) & 0x7fff;
    if (((smr ^ stream) & 0xffff & ~mask) == 0) {
      ++matches; p.slot = i; p.smr = smr; p.s2cr = read(0xc00 + 4 * i);
      if (mask) { p.error = "firmware UFS mapping is masked"; return p; }
    }
  }
  if (matches != 1) { p.error = "no exclusive firmware UFS mapping"; return p; }
  if ((p.s2cr >> 16) & 3) { p.error = "firmware UFS mapping is not TRANSLATE"; return p; }
  p.bank = p.s2cr & 255;
  if (p.bank >= banks) { p.error = "firmware UFS bank not implemented"; return p; }
  for (unsigned i = 0; i < count; ++i) {
    uint32_t smr = read(0x800 + 4 * i);
    if (i == p.slot || !(smr & 0x80000000U)) continue;
    uint32_t s2cr = read(0xc00 + 4 * i);
    if (((s2cr >> 16) & 3) == 0 && (s2cr & 255) == p.bank) { p.error = "firmware UFS bank is shared"; return p; }
  }
  if (read((p.pages + p.bank) * 4096) & 1) { p.error = "firmware UFS bank translates"; return p; }
  if (!(read(0x1800 + 4 * p.bank) & 1) && (dma >= (1ULL << 32) || bytes > (1ULL << 32) - dma)) {
    p.error = "firmware UFS bank is 32-bit"; return p;
  }
  p.reuse = true; p.firmware = true;
  return p;
}

// The UFS stream's context bank: the top bank first.''')
replace('''  if (!retryable(first.error)) return first;''', '''  if (!retryable(first.error)) return first;
  if (!__builtin_strcmp(first.error, "conflicting UFS stream mapping")) {
    UfsSmmuPlan adopted = planFirmwareUfsBypass(stream, dma, bytes, read);
    if (!adopted.error) return adopted;
  }''')
replace('''  if (p.error) return p.error;
  unsigned cb = (p.pages + p.bank) * 4096;''', '''  if (p.error) return p.error;
  unsigned cb = (p.pages + p.bank) * 4096;
  if (p.firmware) {
    // Adopted unchanged: prove it is still exactly the planned pass-through, write nothing.
    (void) stream;
    if (read(0x800 + 4 * p.slot) != p.smr || read(0xc00 + 4 * p.slot) != p.s2cr || (read(cb) & 1))
      return "firmware UFS mapping changed";
    return nullptr;
  }''')
header.write_text(text)
print('X910 firmware UFS bypass adoption added to', header)
