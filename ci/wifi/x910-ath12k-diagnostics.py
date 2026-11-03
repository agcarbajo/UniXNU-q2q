"""Add read-only X910 crash diagnostics to the pinned Ath12kWifi checkout."""
from pathlib import Path
import sys

source = Path(sys.argv[1]) / 'src/nw_wifi.c'
text = source.read_text()
old = '  done = 1;\n  const volatile u8 *m = nw_board->bar;'
if text.count(old) != 1:
    raise SystemExit('Pinned Ath12kWifi RDDM tap changed')
new = '''  done = 1;
  /* Capture the host paging region before recovery tears it down. A later WMI
   * timeout runs after the reset and cannot diagnose the original firmware fault. */
  if (g_hw) {
    struct ath12k *ar = ath12k_ah_to_ar(g_hw->priv, 0);
    const u8 *mac = g_hw->wiphy->perm_addr;
    lx_log(3, "x910 rddm: station mac %02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    pci_state();
    ddr_check(ar->ab);
  }
  const volatile u8 *m = nw_board->bar;'''
source.write_text(text.replace(old, new))
print('X910: capture paging memory and PCI state at the first RDDM callback')
