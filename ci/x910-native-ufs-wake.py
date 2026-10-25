"""X910: wake the UFS device with START STOP UNIT (ACTIVE) before sheng's NOP OUT.

Hardware 2026-10-26 (native-ufs-probe=22): the UEFI's last completed command, just before hand-off, is a COMMAND UPIU
to the UFS Device W-LUN 0xd0 with CDB 1b 00 00 00 30 00 - START STOP UNIT, POWER CONDITION 3 (UFS-PowerDown) - and
its RESPONSE reports success. The link and controller stay active, but the device is powered down; sheng sends NOP OUT
first and only then the waking START STOP UNIT, and on this tablet the NOP OUT never completes.

x910-ufs-wake-first=1: right after the request list is started, send START STOP UNIT with POWER CONDITION ACTIVE to
W-LUN 0xd0 (as Linux's runtime resume does), record the outcome in the root failure report, then continue with
sheng's unchanged sequence (NOP OUT, power-mode query, its own conditional wake, LUN ready).
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
old = '''  if (!hostStartQueue (why)) return false;
  gRootDiagnostic.stage = "NOP OUT";
'''
new = '''  if (!hostStartQueue (why)) return false;
  {
    unsigned wakeFirst = 0;
    PE_parse_boot_argn ("x910-ufs-wake-first", &wakeFirst, sizeof (wakeFirst));
    if (wakeFirst == 1) {
      gRootDiagnostic.stage = "X910 wake (START STOP UNIT ACTIVE)";
      uint8_t ssu[6] = { 0x1b, 0, 0, 0, 0x10, 0 };
      uint32_t scsi = 0xffff;
      IOReturn woke = hostScsi (0xd0, ssu, 6, false, 0, 2000000, &scsi);
      snprintf (gX910LinkText, sizeof (gX910LinkText), "X910 WAKE ssu-active result=%08x scsi=%x ocs=%x sense=%06x\\n",
                woke, scsi, gRootDiagnostic.ocs, gRootDiagnostic.sense);
      if (woke != kIOReturnSuccess) {
        gHost.ready = false;
        *why = "X910 wake (START STOP UNIT ACTIVE)";
        return false;
      }
      IOSleep (50);
    }
  }
  gRootDiagnostic.stage = "NOP OUT";
'''
if text.count(old) != 1:
    raise SystemExit('Pinned NativeUfs bring-up context changed')
source.write_text(text.replace(old, new))
print('X910 wake-first START STOP UNIT added to', source)
