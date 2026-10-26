"""X910: let NativeRamBlock's Data seed lookup wait for the volume, and report what it saw if the volume never appears.

Hardware 2026-10-26: with the owner root and SystemOS mounted, sheng's Data mount panics "offline scratch Data mount
failed: 2" (ENOENT from native_ram_block_volume_seed). The lookup runs once, right when launchd is about to start, while
the RAM disk's IOMedia, its APFS container and the Data volume are published asynchronously after IOBSD.

native_ram_block_volume_seed now retries the unchanged selection every 100 ms for up to 30 s. If the volume still is not
there it panics with the RAM disk state (started, logical/resident bytes, persistence state) and every IOMedia's BSD name,
content and UUID, so the next step is decided from facts. Selection, persistence and the mount itself are unchanged.
"""
import sys
from pathlib import Path

source = Path(sys.argv[1])
text = source.read_text()
old = '''extern "C" int native_ram_block_volume_seed(const char *uuid,dev_t *device,char *path,size_t size,
    uint64_t *base,uint64_t *bytes,uint8_t digest[32]) {
  if(!base || !bytes || !digest)return 0;
  *base=0;*bytes=0;memset(digest,0,32);
  return selectVolumeSeed(uuid,device,path,size,base,bytes,digest);
}'''
new = '''static void x910DataSeedReport(const char *uuid)
{
  static char report[1800];
  int used = snprintf(report, sizeof(report),
                      "X910 DATA SEED: volume %s not found after 30 s\\n"
                      "ram ready=%u bytes=%llx sparse=%u resident=%llx persist state=%u pages=%llx first=%llx\\n",
                      uuid ? uuid : "-", native_ram_block_ready, (unsigned long long)native_ram_block_bytes,
                      native_ram_block_sparse, (unsigned long long)native_ram_block_resident_bytes,
                      native_data_persist_state, (unsigned long long)native_data_persist_pages,
                      (unsigned long long)native_data_persist_first_lba);
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
  unsigned listed = 0;
  while (iterator && used < int(sizeof(report)) - 160 && listed < 14) {
    IOService *media = OSDynamicCast(IOService, iterator->getNextObject());
    if (!media) break;
    OSString *id = OSDynamicCast(OSString, media->getProperty("UUID"));
    OSString *bsd = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    OSString *content = OSDynamicCast(OSString, media->getProperty("Content"));
    OSNumber *bytes = OSDynamicCast(OSNumber, media->getProperty("Size"));
    used += snprintf(report + used, sizeof(report) - used, "%s %s size=%llx uuid=%s\\n",
                     bsd ? bsd->getCStringNoCopy() : "-", content ? content->getCStringNoCopy() : "-",
                     bytes ? bytes->unsigned64BitValue() : 0ULL, id ? id->getCStringNoCopy() : "-");
    ++listed;
  }
  OSSafeReleaseNULL(iterator);
  OSSafeReleaseNULL(matching);
  panic("%s", report);
}

extern "C" int native_ram_block_volume_seed(const char *uuid,dev_t *device,char *path,size_t size,
    uint64_t *base,uint64_t *bytes,uint8_t digest[32]) {
  if(!base || !bytes || !digest)return 0;
  for (unsigned attempt = 0; attempt < 300; ++attempt) {   // X910: the volume is published asynchronously
    *base=0;*bytes=0;memset(digest,0,32);
    if (selectVolumeSeed(uuid,device,path,size,base,bytes,digest)) return 1;
    IOSleep(100);
  }
  x910DataSeedReport(uuid);
  return 0;
}'''
if text.count(old) != 1:
    raise SystemExit('Pinned NativeRamBlock seed lookup changed')
source.write_text(text.replace(old, new))
print('X910 Data seed wait/report added to', source)
