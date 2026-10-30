#!/usr/bin/env python3
"""Build UniXNU's NativeHidInput for the X910 (CI copy of scripts/build-native-hid-input.py, without Apple binaries on the runner).

Same source and the same generated IOHIDDevice ABI header. The checks over the original image run locally on the candidate
(tools/record_x910_hid_abi.py, which executes the upstream script's own checks), and ci/x910-hid-abi.json carries the slot table
they accepted: the method addresses the compiled vtable imports. The initializer is staged as in the CI display builder (LLVM 18
cannot globalize Mach-O symbols, so only the owned initializer gets N_EXT).
"""
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-hid-input'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho


def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=120, **kw)


def require(value, why):
    if not value:
        raise SystemExit(why)


abi = json.loads((ROOT / 'scripts/x910-hid-abi.json').read_text())
require(abi['original_text_sha256'] == '7e4c5104814f2e773d5be13d092dec53ee44ba5a61a81264b229780406e86b72', 'Exact original HID reference')
vptr = int(abi['vtable'], 16)
methods = [int(m, 16) for m in abi['methods']]
require(len(methods) == 207, 'Exact original 207-slot device')
header = '''// Generated from the qualified original image; opaque private storage only.
#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOMemoryDescriptor.h>
class IOHIDDevice : public IOService {
 OSDeclareAbstractStructors(IOHIDDevice)
 unsigned char originalPrivate[0xd0-sizeof(IOService)];
public:
 using IOService::setProperty;
 IOReturn Dispatch(IORPC) override;
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 bool setProperty(const OSSymbol *,OSObject *) override;
 bool willTerminate(IOService *,IOOptionBits) override;
 IOReturn configureReport(IOReportChannelList *,IOReportConfigureAction,void *,void *) override;
 IOReturn updateReport(IOReportChannelList *,IOReportUpdateAction,void *,void *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 IOReturn message(UInt32,IOService *,void *) override;
 IOReturn newUserClient(task_t,void *,UInt32,OSDictionary *,IOUserClient **) override;
'''
for i in range(168, 207):
    if i == 171:
        header += ' virtual OSString *newTransportString() const;\n'
    elif i == 173:
        header += ' virtual OSString *newProductString() const;\n'
    elif i == 180:
        header += ' virtual IOReturn newReportDescriptor(IOMemoryDescriptor **) const=0;\n'
    elif i == 181:
        header += ' virtual IOReturn handleReport(IOMemoryDescriptor *,unsigned,IOOptionBits);\n'
    else:
        header += f' virtual __attribute__((deprecated("Unqualified prototype: never call from source"))) void opaque{i}();\n'
header += '};\nstatic_assert(sizeof(IOHIDDevice)==0xd0,"Original device instance size");\n'
(OUT / 'OriginalHidDeviceABI.h').write_text(header)
print(run([sys.executable, ROOT / 'scripts/build-arm-driver.py', 'NativeHidInput'], text=True), end='')

obj = OUT / 'NativeHidInput.o'
raw = obj.read_bytes()
om = macho(raw)
st = next(p for c, p, _ in om['commands'] if c == 2)
sy, count, strings, _ = struct.unpack_from('<4I', raw, st + 8)
symbols = []
for i in range(count):
    ix, kind, section, _, va = struct.unpack_from('<IBBHQ', raw, sy + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind))
vt = next(va for name, va, _ in symbols if name == '__ZTV14NativeHidInput')
relocations = {}
for segment in om['segments']:
    for sec in segment['sections']:
        if sec['va'] <= vt < sec['va'] + sec['size']:
            ro, rc = struct.unpack_from('<2I', raw, sec['command_offset'] + 56)
            for i in range(rc):
                offset, bits = struct.unpack_from('<2I', raw, ro + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[sec['va'] + offset] = symbols[bits & 0xffffff][0]
compiled = [relocations.get(vt + 16 + i * 8) for i in range(207)]
require(all(compiled) and vt + 16 + 207 * 8 not in relocations, 'Compiled exact 207-slot vtable')
bindings = {'__ZN11IOHIDDeviceC2EPK11OSMetaClass': 0xfffffff004ad94f8, '__ZN11IOHIDDeviceD2Ev': 0xfffffff004ad9518,
            '__ZN11IOHIDDevice10gMetaClassE': 0xfffffff006ae6fa0, '__ZTV11IOHIDDevice': vptr - 16}
owned = []
for i, name in enumerate(compiled):
    if name.startswith(('__ZN14NativeHidInput', '__ZNK14NativeHidInput')):
        owned.append(i)
    elif '11IOHIDDevice' in name:
        bindings[name] = methods[i]
require(owned == [0, 1, 7, 14, 86, 87, 111, 171, 173, 180], 'Only qualified HID methods overridden')
undefined = {n for n, _, k in symbols if k == 1 and '11IOHIDDevice' in n}
require(undefined <= set(bindings), 'All original HID imports bound: %s' % sorted(undefined - set(bindings)))
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {n}\n.set {n}, {a:#x}\n' for n, a in sorted(bindings.items()) if n in undefined))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', asm, '-o', OUT / 'hid-original-bindings.o'])

llvm = Path(run(['brew', '--prefix', 'llvm@18'], text=True).strip()) / 'bin'
late = OUT / 'NativeHidInput.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', obj, late])
data = bytearray(late.read_bytes())
meta = macho(data)
sym_at = next(at for c, at, _ in meta['commands'] if c == 2)
table, count, strings, _ = struct.unpack_from('<4I', data, sym_at + 8)
changed = 0
for k in range(count):
    entry = table + k * 16
    ix, kind, section, desc, value = struct.unpack_from('<IBBHQ', data, entry)
    if data[strings + ix:data.index(0, strings + ix)].decode() == '__GLOBAL__sub_I_NativeHidInput.cpp':
        require(kind == 0x0e and section, 'One defined local owned initializer')
        data[entry + 4] = kind | 1
        changed += 1
require(changed == 1, 'Exactly one owned initializer exported')
late.write_bytes(data)


def code(p):
    b = p.read_bytes()
    s = next(s for g in macho(b)['segments'] for s in g['sections'] if s['name'] == '__text')
    return b[s['fileoff']:s['fileoff'] + s['size']]


require(code(obj) == code(late), 'Owned code unchanged by deferred metaclass registration')
print('NativeHidInput compiled against the recorded original IOHIDDevice ABI; runtime not yet tested')
