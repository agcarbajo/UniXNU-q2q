"""Allow the reviewed 16 GiB NativeRamBlock seed through the owner Data mount gate.

The pinned XNU patch has a separate 2 GiB typed-provider guard. Keep the dense
mdev guard unchanged and report the Data mount stage if a later operation fails.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
replacements = [
    ('+\tif (!size || size > UINT64_C(0x80000000) || (size & 4095)) goto done;',
     '+\tnative_owner_data_seed_size = size; if (!size || size > UINT64_C(0x400000000) || (size & 4095)) goto done;'),
    ('+\tif (data_error) panic("offline scratch Data mount failed: %d", data_error);',
     '+\tif (data_error) panic("X910 DATA MOUNT: err=%d stage=%d readonly=%d dev=%x seed=%llx path=%s", data_error, native_owner_data_stage, native_owner_data_readonly, (unsigned)native_owner_data_device, (unsigned long long)native_owner_data_seed_size, native_owner_data_device_path);'),
]
for old, new in replacements:
    if text.count(old) != 1:
        raise SystemExit('Pinned owner Data patch anchor changed: ' + old)
    text = text.replace(old, new)
source.write_text(text)
print('X910 owner Data: typed seed cap 16 GiB; dense mdev guard unchanged; mount failure stages enabled')
