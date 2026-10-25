"""X910: select the UTRD DW0 header the firmware itself uses, and dump the firmware's last command in full.

Hardware 2026-10-26 (native-ufs-probe=6): the UEFI's request list at 0xbda78000 holds a completed UTRD (OCS 0) with
DW0 = 0x01000000 (command type 0, interrupt bit set), UCD at 0xbda78400, response offset 8 / length 16 dwords and
PRDT offset 0x18 dwords; its UPIU is a COMMAND to W-LUN 0xd0 with task tag 0xe9. sheng writes DW0 = 0x10000000 |
direction (command type 1, no interrupt bit), and on this controller (UFSHCI 4.0) its NOP OUT never completes.

- x910-ufs-dw0=1: DW0 base 0x01000000 (the firmware's: CT 0, I 1); x910-ufs-dw0=2: 0x11000000 (CT 1, I 1).
  Default (absent/0): sheng's 0x10000000. The data-direction bits are kept as sheng computes them.
- native-ufs-probe=22: read-only dump of the firmware's UTRD 0 (8 words), its command UPIU (8 words incl. the CDB)
  and response UPIU at the offset the UTRD names (8 words).
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
commands = root/'RootCommands.h'
text = commands.read_text()
old = '  words[0] = 0x10000000U | (direction << 25);\n'
if text.count(old) != 1:
    raise SystemExit('Pinned RootCommands.h UTRD context changed')
commands.write_text(text.replace(old, '  words[0] = x910UtrdDw0Base () | (direction << 25);\n'))

source = root/'NativeUfs.cpp'
text = source.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Pinned NativeUfs.cpp context changed: ' + old[:60])
    text = text.replace(old, new)


replace('static uintptr_t mapPhys (uint64_t phys, uint64_t length, OSSharedPtr<IOMemoryMap> &keep)\n{', '''static uint32_t x910UtrdDw0Base (void)
{
  static int cached = -1;
  if (cached < 0) {
    uint32_t mode = 0;
    PE_parse_boot_argn ("x910-ufs-dw0", &mode, sizeof (mode));
    cached = mode == 1 ? 1 : mode == 2 ? 2 : 0;
  }
  return cached == 1 ? 0x01000000U : cached == 2 ? 0x11000000U : 0x10000000U;
}

static uintptr_t mapPhys (uint64_t phys, uint64_t length, OSSharedPtr<IOMemoryMap> &keep)
{''')
replace('''  if (probe == 20) {''', '''  if (probe == 22) {
    OSSharedPtr<IOMemoryMap> hcMap, listMap, ucdMap;
    uintptr_t hc = mapPhys (UFS_HC_BASE, UFS_HC_SIZE, hcMap);
    if (!hc) panic ("X910 UFS FW CMD: controller map failed");
    uint64_t listPhys = (uint64_t (read32 (hc, 0x54)) << 32) | read32 (hc, 0x50);
    uintptr_t list = mapPhys (listPhys, 4096, listMap);
    if (!list) panic ("X910 UFS FW CMD: list map failed %llx", listPhys);
    uint32_t u[8];
    for (unsigned i = 0; i < 8; ++i) u[i] = read32 (list, 4 * i);
    uint64_t ucdPhys = (uint64_t (u[5]) << 32) | u[4];
    uint32_t c[8] = {}, r[8] = {};
    uintptr_t ucd = ucdPhys ? mapPhys (ucdPhys & ~4095ULL, 8192, ucdMap) : 0;
    if (ucd) {
      uintptr_t at = ucd + (ucdPhys & 4095);
      unsigned rsp = (u[6] >> 16) * 4;
      for (unsigned i = 0; i < 8; ++i) { c[i] = read32 (at, 4 * i); r[i] = rsp < 4096 ? read32 (at, rsp + 4 * i) : 0; }
    }
    panic ("X910 UFS FW CMD (read-only)\\n"
           "list=%llx hcs=%08x db=%08x rsr=%08x ie=%08x utriacr=%08x\\n"
           "utrd %08x %08x %08x %08x %08x %08x %08x %08x\\n"
           "cmd  %08x %08x %08x %08x %08x %08x %08x %08x\\n"
           "rsp  %08x %08x %08x %08x %08x %08x %08x %08x",
           listPhys, read32 (hc, 0x30), read32 (hc, 0x58), read32 (hc, 0x60), read32 (hc, 0x24), read32 (hc, 0x4c),
           u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7],
           r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]);
  }
  if (probe == 20) {''')
source.write_text(text)
print('X910 UTRD DW0 selection and firmware command dump added under', root)
