#!/usr/bin/env python3
"""Build sheng's NativeUfs and the VerifiedRoot policy carrier for the X910 (Galaxy Tab S9 Ultra) root partition.

Compile-only on the runner. The original IOStorageFamily bindings and the owner-signed policies are both derived
locally from Apple inputs that never enter this public repository:

- Phase 1 (no ci/x910-storage-bindings.json): compile NativeUfs.o and export its relocations/imports so the local
  audit can find the original IOBlockStorageDevice vtable, constructor, destructor and metaclass in the pinned
  kernelcache (fixed by the -0x1000000 slide, independent of this source kernel).
- Phase 2 (bindings present): add the audited absolute aliases to libNativeUfs.a, exactly as sheng's
  build-native-virtio-bindings.py does for its block transports.

libX910VerifiedRoot.a carries the real VerifiedRoot C code and XNU crypto adapter, plus a zero-filled policy region
(owner key, up to two signed manifests, count 0). The count stays 0, so no window can be published, until the local
step writes an owner key and the signed OS/SystemOS manifests into that region. Nothing here weakens verification.
"""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
import native_prelink as np

OS_CAPACITY = 47482880          # >= 544 + 1483785 * 32, 4 KiB multiple
SYSTEMOS_CAPACITY = 25806848    # >= 544 + 806400 * 32, 4 KiB multiple


def run(command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, **kwargs)


def recipe_command(recipe, source, destination, extra):
    record = json.loads(recipe.read_text().strip().rstrip(','))
    arguments, command, index = record['arguments'], [], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    # This early archive pass runs before XNU exports some header folders the recipe names; -Wmissing-include-dirs is
    # an error there, so create the (empty) folders instead of dropping flags.
    for i, value in enumerate(command):
        folder = command[i + 1] if value in ('-I', '-isystem', '-iquote') and i + 1 < len(command) else             value[2:] if value.startswith('-I') and len(value) > 2 else None
        if folder:
            Path(record['directory'], folder).mkdir(parents=True, exist_ok=True)
    return command + extra, record['directory']


# Public IOStorageFamily headers, as audit-storage-interface.py prepares them (its live-kernel audit runs locally).
abi = ROOT / 'build/storage-abi'
headers = abi / 'include/IOKit/storage'
headers.mkdir(parents=True, exist_ok=True)
for path in (ROOT / 'refer/IOStorageFamily').glob('*.h'):
    shutil.copyfile(path, headers / path.name)
sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
shutil.copyfile(sdk / 'usr/include/TargetConditionals.h', abi / 'include/TargetConditionals.h')

layout = (ROOT / 'drivers/NativeUfs/RootLayout.h').read_text()
if 'static const char wanted[] = "ipadosroot";' not in layout:
    raise SystemExit('NativeUfs must select the X910 ipadosroot partition, never Android userdata')
run([sys.executable, ROOT / 'scripts/build-arm-driver.py', 'NativeUfs'])
out = ROOT / 'build/native-ufs'
obj_path = out / 'NativeUfs.o'
obj = obj_path.read_bytes()
image = np.macho(obj)
offset = next(at for cmd, at, size in image['commands'] if cmd == 2)
sy, count, st, _ = struct.unpack_from('<4I', obj, offset + 8)
symbols = []
for i in range(count):
    ix, kind, _, _, value = struct.unpack_from('<IBBHQ', obj, sy + i * 16)
    symbols.append((obj[st + ix:obj.index(b'\0', st + ix)].decode(), value, kind))
vtable = next(v for name, v, kind in symbols if name == '__ZTV14NativeUfsBlock')
relocations = {}
for segment in image['segments']:
    for section in segment['sections']:
        if section['va'] <= vtable < section['va'] + section['size']:
            ro, rc = struct.unpack_from('<II', obj, section['command_offset'] + 56)
            for i in range(rc):
                address, word = struct.unpack_from('<II', obj, ro + i * 8)
                if word >> 27 & 1 and word >> 28 == 0 and word >> 25 & 3 == 3:
                    relocations[section['va'] + address] = symbols[word & 0xffffff][0]
slots = [relocations.get(vtable + 16 + i * 8) for i in range(190)]
if not all(slots) or vtable + 16 + 190 * 8 in relocations:
    raise SystemExit('NativeUfsBlock vtable is not the 190-slot IOBlockStorageDevice shape')
undefined = sorted(name for name, value, kind in symbols if kind == 1)
storage_imports = [name for name in undefined if 'IOBlockStorageDevice' in name]
(out / 'x910-native-ufs-imports.json').write_text(json.dumps(dict(
    object_sha256=hashlib.sha256(obj).hexdigest(), vtable_slots=slots,
    storage_imports=storage_imports, undefined=undefined), indent=2) + '\n')

bindings_path = ROOT / 'scripts/x910-storage-bindings.json'
if bindings_path.is_file():
    record = json.loads(bindings_path.read_text())
    bindings = {name: int(value, 16) for name, value in record['bindings'].items()}
    if record['object_sha256'] != hashlib.sha256(obj).hexdigest():
        raise SystemExit('Bindings were audited for a different NativeUfs object')
    if sorted(bindings) != storage_imports:
        raise SystemExit('Bindings do not cover exactly the original storage imports')
    assembly = out / 'original-bindings.S'
    assembly.write_text(''.join(f'.globl {n}\n.set {n}, {v:#x}\n' for n, v in sorted(bindings.items())))
    run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Werror', '-Wno-incompatible-sysroot',
         '-c', assembly, '-o', out / 'original-bindings.o'])
    archive = out / 'libNativeUfs.a'
    archive.unlink(missing_ok=True)
    run(['xcrun', 'ar', 'rcs', archive, obj_path, out / 'original-bindings.o'])

# VerifiedRoot code and the owner policy carrier.
vr = ROOT / 'build/x910-verified-root'
vr.mkdir(parents=True, exist_ok=True)
recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
objects = []
for source in (ROOT / 'kits/VerifiedRoot/src/verified_root.c', ROOT / 'kits/VerifiedRoot/adapters/xnu_crypto.c'):
    destination = vr / (source.stem + '.o')
    command, directory = recipe_command(recipe, source, destination,
                                        ['-I' + str(ROOT / 'kits/VerifiedRoot/include'), '-Werror'])
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
    (vr / (source.stem + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stderr[-3500:])
    objects.append(destination)
carrier = vr / 'policy-carrier.S'
carrier.write_text(f'''// X910 owner policy carrier: zero until the local step writes the owner key and signed manifests.
.section __DATA_CONST,__const
.balign 4096
.globl _x910_verified_root_carrier
_x910_verified_root_carrier:
.ascii "X910VRPL"
.quad 1, {OS_CAPACITY}, {SYSTEMOS_CAPACITY}
.balign 8
.globl _verified_root_policy_count
_verified_root_policy_count:
.quad 0
.globl _verified_root_policies
_verified_root_policies:
.quad _x910_policy_os, 0
.quad _x910_policy_systemos, 0
.globl _verified_root_key
_verified_root_key:
.space 395
.balign 4096
_x910_policy_os:
.space {OS_CAPACITY}
.balign 4096
_x910_policy_systemos:
.space {SYSTEMOS_CAPACITY}
''')
sdk_ios = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk_ios, '-c', carrier, '-o', vr / 'policy-carrier.o'])
objects.append(vr / 'policy-carrier.o')
for path in objects:
    data = path.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Every verified-root object must be ordinary ARM64')
archive = vr / 'libX910VerifiedRoot.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, *objects])
(vr / 'x910-verified-root-report.json').write_text(json.dumps(dict(
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), policy_count=0,
    os_capacity=OS_CAPACITY, systemos_capacity=SYSTEMOS_CAPACITY,
    bindings_linked=bindings_path.is_file(), apple_inputs_included=False), indent=2) + '\n')
print('NativeUfs (ipadosroot) and VerifiedRoot carrier built; bindings', 'linked' if bindings_path.is_file() else 'pending local audit')
