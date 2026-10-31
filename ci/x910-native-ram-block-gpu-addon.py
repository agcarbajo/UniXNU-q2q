"""X910: hand the boot payload's GPU addon to the kernel's addon install (zzy-native-gpu-addon-mount.patch).

The base tree's NativeRamBlock has no native_ram_block_gpu_addon, and the newer revision that does (UniXNU 82dd277, d4d22d98) also
changes how Data is stored, which the X910 keeps as it is. Only that function of d4d22d98 is added, unchanged in behaviour: the range
in /chosen/memory-map/GpuAddon, checked against the boot memory, as a kernel pointer. Without a range it returns 0.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
anchor = 'extern "C" int native_ram_block_volume_seed(const char *uuid,dev_t *device,char *path,size_t size,'
if text.count(anchor) != 1 or 'native_ram_block_gpu_addon' in text:
    raise SystemExit('Pinned NativeRamBlock context changed')
function = '''// The GPU addon the loader placed after the trust caches (/chosen/memory-map/GpuAddon), for the kernel's boot-time addon install
// (kern_exec.c). Boot memory, like the seed: never freed here. Returns 0 without one.
extern "C" int native_ram_block_gpu_addon(const uint8_t **data,uint64_t *bytes) {
  if(!data || !bytes)return 0;
  *data=nullptr;*bytes=0;
  IORegistryEntry *map=IORegistryEntry::fromPath("/chosen/memory-map",gIODTPlane);
  if(!map)return 0;
  OSData *range=OSDynamicCast(OSData,map->getProperty("GpuAddon"));
  uint64_t physical=0,size=0;
  if(range && range->getLength()==16){
    memcpy(&physical,range->getBytesNoCopy(),8);
    memcpy(&size,static_cast<const uint8_t *>(range->getBytesNoCopy())+8,8);
  }
  map->release();
  if(!size || size>(UINT64_C(64)<<20) || (physical&4095) || physical<gPhysBase || size>gPhysSize || physical-gPhysBase>gPhysSize-size)return 0;
  *data=reinterpret_cast<const uint8_t *>(ml_static_ptovirt(physical));
  if(!*data)return 0;
  *bytes=size;
  return 1;
}

'''
source.write_text(text.replace(anchor, function + anchor))
