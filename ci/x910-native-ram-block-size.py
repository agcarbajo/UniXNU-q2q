"""X910: permit a bounded 16 GiB logical Data seed in the pinned sparse driver.

Only the logical size limit changes. Packed input remains capped at 256 MiB,
extent checks are unchanged, and the pointer table is at most 32 MiB on ARM64.
Apply only for the new 16 GiB seed; c3 intentionally retains the old limit.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
old = 'if(!size || size>0x80000000 || (size&4095) || count>65536 || count>(n-32)/16)return false;'
new = 'if(!size || size>0x400000000ULL || (size&4095) || count>65536 || count>(n-32)/16)return false;'
if text.count(old) != 1:
    raise SystemExit('Pinned NativeRamBlock sparse logical-size guard changed')
source.write_text(text.replace(old, new))
print('X910 NativeRamBlock: logical Data cap 16 GiB; pointer table <=32 MiB; packed/extent guards unchanged')
