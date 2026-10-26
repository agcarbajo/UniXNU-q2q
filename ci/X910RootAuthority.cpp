// SPDX-License-Identifier: BSD-2-Clause-Patent
// X910 owner-root authority: the volume queries the owner-root XNU patches call, answered by NativeUfs.
//
// sheng implements native_verified_block_root_authorized / _manifest_sha384 / _volume_device inside
// NativeVirtioBlock, whose verifiedVolumeQuery walks an IOMedia's providers and, for a UFS window, defers to
// NativeUfs (native_ufs_block_authenticates). The X910 has no virtio device, so this file carries only that UFS
// path, unchanged in its decisions: exactly one IOMedia must match, it must carry a 36-character UUID, and the
// first NativeUfs window found among its providers must authenticate that UUID against its owner-signed manifest.
#include <IOKit/IOService.h>
#include <IOKit/IOBSD.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/crypto/sha2.h>
#include <sys/types.h>
#include <sys/param.h>

extern "C" {
int native_ufs_block_authenticates(IOService *, const char *, uint8_t *);
int native_verified_block_root_authorized(dev_t device);
int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48]);
int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size);
volatile uint32_t native_verified_block_root_media_matches = 0;
volatile uint32_t native_verified_block_root_provider_depth = 0;
volatile uint32_t native_verified_block_root_uuid_match = 0;
char native_verified_block_root_uuid[37] = {};
}

static int verifiedVolumeQuery(dev_t device, const char *expectedUUID, uint8_t *manifestDigest)
{
  if (device == NODEV) return 0;
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(2);
  OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(static_cast<uint32_t>(device))), 32);
  OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(static_cast<uint32_t>(device))), 32);
  if (!matching || !properties || !mjr || !mnr) {
    OSSafeReleaseNULL(mnr); OSSafeReleaseNULL(mjr); OSSafeReleaseNULL(properties); OSSafeReleaseNULL(matching);
    return 0;
  }
  bool ready = properties->setObject(kIOBSDMajorKey, mjr) && properties->setObject(kIOBSDMinorKey, mnr) &&
               matching->setObject("IOPropertyMatch", properties);
  mnr->release(); mjr->release(); properties->release();
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  matching->release();
  if (!iterator) return 0;
  bool authenticated = false;
  unsigned matches = 0, depth = 0;
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++matches != 1) { authenticated = false; break; }
    OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
    if (!uuid || uuid->getLength() != 36 || (expectedUUID && strcmp(expectedUUID, uuid->getCStringNoCopy()))) continue;
    strlcpy(native_verified_block_root_uuid, uuid->getCStringNoCopy(), sizeof(native_verified_block_root_uuid));
    for (IOService *provider = media; provider && depth < 64; provider = provider->getProvider()) {
      ++depth;
      int ufs = native_ufs_block_authenticates(provider, uuid->getCStringNoCopy(), manifestDigest);
      if (ufs >= 0) {
        authenticated = ufs == 1;
        native_verified_block_root_uuid_match = authenticated;
        break;
      }
    }
  }
  iterator->release();
  native_verified_block_root_media_matches = matches;
  native_verified_block_root_provider_depth = depth;
  if (!authenticated || matches != 1) {
    if (manifestDigest) bzero(manifestDigest, SHA384_DIGEST_LENGTH);
    return 0;
  }
  return 1;
}

extern "C" int native_verified_block_root_authorized(dev_t device)
{
  return verifiedVolumeQuery(device, nullptr, nullptr);
}

extern "C" int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48])
{
  if (!digest) return 0;
  bzero(digest, SHA384_DIGEST_LENGTH);
  if (!uuid || strlen(uuid) != 36) return 0;
  return verifiedVolumeQuery(device, uuid, digest);
}

extern "C" int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size)
{
  if (!uuid || !device || !path || size < 6 || strlen(uuid) != 36) return 0;
  *device = NODEV;
  path[0] = 0;
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(1);
  OSString *wanted = OSString::withCString(uuid);
  if (!matching || !properties || !wanted) {
    OSSafeReleaseNULL(wanted); OSSafeReleaseNULL(properties); OSSafeReleaseNULL(matching);
    return 0;
  }
  bool ready = properties->setObject("UUID", wanted) && matching->setObject("IOPropertyMatch", properties);
  wanted->release(); properties->release();
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  matching->release();
  if (!iterator) return 0;
  unsigned matches = 0;
  bool authenticated = false;
  dev_t candidate = NODEV;
  char candidatePath[64] = {};
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++matches != 1) { authenticated = false; break; }
    OSString *identity = OSDynamicCast(OSString, media->getProperty("UUID"));
    OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    OSNumber *mjr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
    OSNumber *mnr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
    if (!identity || strcmp(identity->getCStringNoCopy(), uuid) || !name || !mjr || !mnr ||
        !name->getLength() || name->getLength() >= sizeof(candidatePath) - 6 || name->getLength() >= size - 6 ||
        mjr->unsigned64BitValue() > 255 || mnr->unsigned64BitValue() > 0xffffff) continue;
    candidate = static_cast<dev_t>(makedev(mjr->unsigned32BitValue(), mnr->unsigned32BitValue()));
    if (!native_verified_block_root_authorized(candidate)) continue;
    snprintf(candidatePath, sizeof(candidatePath), "/dev/%s", name->getCStringNoCopy());
    authenticated = true;
  }
  iterator->release();
  if (!authenticated || matches != 1) return 0;
  *device = candidate;
  strlcpy(path, candidatePath, size);
  return 1;
}
