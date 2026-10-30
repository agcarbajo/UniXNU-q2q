#!/usr/bin/env python3
"""Build the staged AGXAccelerator class (drivers/NativeAdreno/NativeAdrenoAccelerator.cpp) as a late object in libNativeAdreno.a
(X910 copy of UniXNU scripts/build-native-adreno-accelerator.py).

Same source, recipe and bindings: the exact original IOAcceleratorES constructor, destructor and metaclass addresses of the
recorded scripts/data/ioaccelerator-base-abi.json. NativePrelink keeps every original kext at its kernelcache address, and a local
prelink of the X910 candidate with IOGPUFamily (tools/prepare_apfs_trustcache_candidate.py --gpu) matched the IOGPUFamily text
hash and all four recorded entry hashes (ci/x910-gpu-inputs.json). Differences: Xcode 16.4's clang has no
-Wunnecessary-virtual-specifier, and the initializer is staged as in the CI display builder (LLVM 18 cannot globalize Mach-O
symbols, so only the owned initializer gets N_EXT). The class's initializer runs only when GpuPowerProbe publishes Metal
(sheng-gpu-metal=1), after the original IOGPUFamily kext has loaded.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-adreno-accelerator'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho


def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=120, **kw)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


plan_path = ROOT / 'scripts/data/ioaccelerator-base-abi.json'
pinned = json.loads((ROOT / 'scripts/x910-gpu-inputs.json').read_text())['accelerator']
if sha(plan_path) != pinned['abi_sha256']:
    sys.exit('The recorded IOAcceleratorES ABI differs from the one checked against the X910 prelink')
plan = json.loads(plan_path.read_text())
if plan['operational_overrides'] or plan['instance_bytes'] != 136 or plan['virtual_entries'] != 168:
    sys.exit('The audited original base is not the expected 136-byte, 168-entry class without operational overrides')

record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
source = ROOT / 'drivers/NativeAdreno/NativeAdrenoAccelerator.cpp'
obj = OUT / 'NativeAdrenoAccelerator.o'
cmd, i, args = [], 0, record['arguments']
while i < len(args):
    v = args[i]
    if v == record['file']:
        cmd.append(str(source))
    elif v == '-o':
        cmd += ['-o', str(obj)]
        i += 1
    elif v == '-Xclang' and args[i + 1].startswith('-W'):
        i += 1
    elif not v.startswith('-W') and v not in ('-MD', '-MP'):
        cmd.append(v)
    i += 1
    if v in ('-MF', '-MJ'):
        i += 1
cmd += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator',
        '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-Wno-overloaded-virtual',
        '-I', str(ROOT / '.cache/xnu-work/osfmk')]
subprocess.run(cmd, cwd=record['directory'], check=True)

raw = obj.read_bytes()
im = macho(raw)
st = next(p for c, p, _ in im['commands'] if c == 2)
at, count, strings, _ = struct.unpack_from('<4I', raw, st + 8)
symbols = []
for k in range(count):
    ix, kind, section, _, va = struct.unpack_from('<IBBHQ', raw, at + k * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind))
bindings = {'__ZN15IOAcceleratorESC2EPK11OSMetaClass': int(plan['protected_compatible_constructor_aliases'][0], 16),
            '__ZN15IOAcceleratorESD2Ev': int(plan['nondeleting_destructor'], 16),
            '__ZN15IOAcceleratorES10gMetaClassE': int(plan['metaclass'], 16)}
undefined = {n for n, _, k in symbols if k == 1 and 'IOAcceleratorES' in n}
extra = undefined - set(bindings)
if extra or not undefined & set(bindings):
    sys.exit('Unexpected original-base imports: %s' % sorted(extra))
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {n}\n.set {n}, {a:#x}\n' for n, a in sorted(bindings.items()) if n in undefined))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', asm, '-o', OUT / 'original-bindings.o'])

llvm = Path(run(['brew', '--prefix', 'llvm@18'], text=True).strip()) / 'bin'
late = OUT / 'NativeAdrenoAccelerator.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', obj, late])
data = bytearray(late.read_bytes())
meta = macho(data)
sym_at = next(at for c, at, _ in meta['commands'] if c == 2)
table, count, strings, _ = struct.unpack_from('<4I', data, sym_at + 8)
changed = 0
for k in range(count):
    entry = table + k * 16
    ix, kind, section, desc, value = struct.unpack_from('<IBBHQ', data, entry)
    if data[strings + ix:data.index(0, strings + ix)].decode() == '__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp':
        if kind != 0x0e or not section:
            sys.exit('The owned initializer is not one defined local symbol')
        data[entry + 4] = kind | 1
        changed += 1
if changed != 1:
    sys.exit('Exactly one owned initializer must be exported')
late.write_bytes(data)


def code(p):
    b = p.read_bytes()
    s = next(s for g in macho(b)['segments'] for s in g['sections'] if s['name'] == '__text')
    return b[s['fileoff']:s['fileoff'] + s['size']]


if code(obj) != code(late):
    sys.exit('Only the owned initializer registration may change')
native = ROOT / 'build/native-adreno/libNativeAdreno.a'
run(['xcrun', 'ar', 'rcs', native, late, OUT / 'original-bindings.o'])
(OUT / 'build-report.json').write_text(json.dumps(dict(
    source_sha256={str(p.relative_to(ROOT)): sha(p) for p in (source, ROOT / 'drivers/NativeMetal/OriginalAcceleratorABI.h')},
    original_abi_sha256=sha(plan_path), bindings={n: hex(a) for n, a in bindings.items()},
    original_instructions_modified=False, runtime_qualified=False), indent=2) + '\n')
print('PASS: staged AGXAccelerator builds against the recorded original IOAcceleratorES imports')
