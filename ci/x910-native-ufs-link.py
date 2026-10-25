"""X910 UFS link-state report and conditional Hibern8 exit for the X910 copy of NativeUfs.

Hardware 2026-10-25: after DarwinBoot's hand-off NativeUfs's first command (NOP OUT) never completes (OCS 0xf,
doorbell held, IS 0) while HCS reports a ready controller (0x10f, UPMCRS=PWR_LOCAL) and all UIC error registers are 0.
On this tablet the UEFI found the link in Hibern8 (M-PHY TX_FSM_STATE 0x41 lane 0 = 1, the value Qualcomm Linux uses
for Hibern8) and had to leave it; sheng's NativeUfs assumes an active link and has no UIC path.

- native-ufs-probe=20: read-only report (no UIC state change): DME_GET of TX_FSM lanes 0/1, RX_FSM lane 0,
  PA_PWRMode and connected lane counts, plus HCS/IS/IE/doorbell/run-stop and the GCC UFS power/clock branches.
- x910-ufs-h8-exit=1: in the root bring-up, only when TX_FSM lane 0 reads exactly 1, send the standard UFSHCI
  DME_HIBERNATE_EXIT and require UIC completion, UHXS and HCS.UPMCRS == PWR_LOCAL (Linux ufshcd_uic_pwr_ctrl's
  success condition). Otherwise nothing is sent. The outcome is appended to the root failure report.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Pinned NativeUfs.cpp context changed: ' + old[:60])
    text = text.replace(old, new)


replace('static uintptr_t mapPhys (uint64_t phys, uint64_t length, OSSharedPtr<IOMemoryMap> &keep)\n', '''// ---- X910: UFS link state through UIC (UFSHCI 2.1: IS 0x20, HCS 0x30, UICCMD 0x90, UCMDARG1..3 0x94..0x9c) ----
static char gX910LinkText[240];

static bool x910UicCommand (uintptr_t hc, uint32_t command, uint32_t arg1, uint32_t extraIs,
                            uint32_t *result, uint32_t *value, uint32_t *isSeen)
{
  unsigned loops = 0;
  while (!(read32 (hc, 0x30) & 0x8) && loops++ < 100000) IODelay (1);   // HCS.UCRDY
  if (!(read32 (hc, 0x30) & 0x8)) { *result = 0x100; if (isSeen) *isSeen = read32 (hc, 0x20); return false; }
  const uint32_t need = 0x400 | extraIs;                                  // IS.UCCS (+ UHXS for Hibern8 exit)
  write32 (hc, 0x20, need);
  write32 (hc, 0x94, arg1);
  write32 (hc, 0x98, 0);
  write32 (hc, 0x9c, 0);
  write32 (hc, 0x90, command);
  uint32_t is = 0;
  for (loops = 0; loops < 500000; ++loops) {
    is = read32 (hc, 0x20);
    if ((is & need) == need) break;
    IODelay (1);
  }
  if (isSeen) *isSeen = is;
  write32 (hc, 0x20, is & need);
  *result = read32 (hc, 0x98) & 0xff;
  if (value) *value = read32 (hc, 0x9c);
  return (is & need) == need && *result == 0;
}

static uint32_t x910DmeGet (uintptr_t hc, uint32_t attribute, uint32_t selector, uint32_t *result)
{
  uint32_t value = 0xffffffffU;
  x910UicCommand (hc, 0x01, (attribute << 16) | selector, 0, result, &value, nullptr);   // DME_GET
  return value;
}

static bool x910Hibern8Exit (uintptr_t hc)
{
  uint32_t result = 0, is = 0, after = 0xffffffffU, afterResult = 0;
  uint32_t before = x910DmeGet (hc, 0x41, 0, &result);
  if (before != 1 || result) {
    snprintf (gX910LinkText, sizeof (gX910LinkText), "X910 LINK tx_fsm=%x get=%x: no hibern8 exit sent\\n", before, result);
    return true;
  }
  bool ok = x910UicCommand (hc, 0x18, 0, 0x80, &result, nullptr, &is);   // DME_HIBERNATE_EXIT, IS.UHXS
  uint32_t upmcrs = (read32 (hc, 0x30) >> 8) & 7;
  after = x910DmeGet (hc, 0x41, 0, &afterResult);
  snprintf (gX910LinkText, sizeof (gX910LinkText),
            "X910 LINK hibern8 exit: uic=%x is=%x upmcrs=%u tx_fsm 1->%x (get %x) ok=%u\\n",
            result, is, upmcrs, after, afterResult, ok && upmcrs == 1);
  return ok && upmcrs == 1;
}

static uintptr_t mapPhys (uint64_t phys, uint64_t length, OSSharedPtr<IOMemoryMap> &keep)
''')
replace('''  gRootDiagnostic.stage = "DMA allocation";
  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions (''', '''  {
    unsigned h8 = 0;
    PE_parse_boot_argn ("x910-ufs-h8-exit", &h8, sizeof (h8));
    if (h8 == 1) {
      gRootDiagnostic.stage = "X910 hibern8 exit";
      if (!x910Hibern8Exit (gHost.hc)) {
        *why = "X910 hibern8 exit failed";
        return false;
      }
    }
  }
  gRootDiagnostic.stage = "DMA allocation";
  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions (''')
replace('''  strlcat (gRootDiagnosticText, gRootMediaText, sizeof (gRootDiagnosticText));''',
        '''  strlcat (gRootDiagnosticText, gX910LinkText, sizeof (gRootDiagnosticText));
  strlcat (gRootDiagnosticText, gRootMediaText, sizeof (gRootDiagnosticText));''')
replace('''  if (probe == 14) {''', '''  if (probe == 20) {
    OSSharedPtr<IOMemoryMap> hcMap, gccMap, memMap;
    uintptr_t hc = mapPhys (UFS_HC_BASE, UFS_HC_SIZE, hcMap);
    uintptr_t gcc = mapPhys (GCC_BASE + 0x77000, 0x1000, gccMap);
    uintptr_t mem = mapPhys (GCC_BASE + 0x9e000, 0x1000, memMap);
    if (!hc || !gcc || !mem) {
      panic ("X910 UFS LINK: map failed");
    }
    uint32_t hcs = read32 (hc, 0x30), hce = read32 (hc, 0x34), is = read32 (hc, 0x20), ie = read32 (hc, 0x24);
    uint32_t db = read32 (hc, 0x58), rsr = read32 (hc, 0x60), pa = read32 (hc, 0x38), dl = read32 (hc, 0x3c);
    uint32_t r[6] = {};
    uint32_t tx0 = x910DmeGet (hc, 0x41, 0, &r[0]), tx1 = x910DmeGet (hc, 0x41, 1, &r[1]);
    uint32_t rx0 = x910DmeGet (hc, 0xc1, 4, &r[2]), pwr = x910DmeGet (hc, 0x1571, 0, &r[3]);
    uint32_t ctx = x910DmeGet (hc, 0x1561, 0, &r[4]), crx = x910DmeGet (hc, 0x1581, 0, &r[5]);
    panic ("X910 UFS LINK (read-only)\\n"
           "hcs=%08x hce=%08x is=%08x ie=%08x db=%08x rsr=%08x uecpa=%08x uecdl=%08x\\n"
           "TX_FSM l0=%x (r%x) l1=%x (r%x) RX_FSM l0=%x (r%x)\\n"
           "PA_PWRMode=%x (r%x) txLanes=%x (r%x) rxLanes=%x (r%x)\\n"
           "GCC gdsc=%08x memgdsc=%08x axi=%08x ahb=%08x unipro=%08x aux=%08x\\n"
           "txsym=%08x rxsym0=%08x rxsym1=%08x ice=%08x aggre=%08x",
           hcs, hce, is, ie, db, rsr, pa, dl, tx0, r[0], tx1, r[1], rx0, r[2], pwr, r[3], ctx, r[4], crx, r[5],
           read32 (gcc, GCC_UFS_PHY_GDSCR - 0x77000), read32 (mem, 0),
           read32 (gcc, GCC_UFS_PHY_AXI_ENABLE - 0x77000), read32 (gcc, GCC_UFS_PHY_AHB_ENABLE - 0x77000),
           read32 (gcc, GCC_UFS_PHY_UNIPRO_CORE_ENABLE - 0x77000), read32 (gcc, GCC_UFS_PHY_PHY_AUX_ENABLE - 0x77000),
           read32 (gcc, GCC_UFS_PHY_TX_SYMBOL0_ENABLE - 0x77000), read32 (gcc, GCC_UFS_PHY_RX_SYMBOL0_ENABLE - 0x77000),
           read32 (gcc, GCC_UFS_PHY_RX_SYMBOL1_ENABLE - 0x77000), read32 (gcc, GCC_UFS_PHY_ICE_CORE_ENABLE - 0x77000),
           read32 (gcc, GCC_AGGRE_UFS_PHY_AXI_ENABLE - 0x77000));
  }
  if (probe == 14) {''')
source.write_text(text)
print('X910 UFS link report and conditional Hibern8 exit added to', source)
