#!/usr/bin/env python3
"""Add the GMU kit and firmware objects to the NativeAdreno archive (X910 copy of sheng's build-native-adreno-kits.py).

Same sources, recipe and required definitions as the pinned sheng builder. Differences: the GMU register/vote headers come
from ci/gpu (generated from sheng's pinned Linux/DT inputs, hash-checked when copied), and only the GMU and SQE firmware are
embedded (the generic SM8550 files of sheng's pinned firmware tree, fetched and hash-checked by ci-source-kernel.py). The zap
shader is board-signed and is not embedded: its weak reference stays empty, so GpuPowerProbe never asks TZ to load one.
"""
import hashlib, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
c_recipe = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json').read_text().strip().rstrip(','))
cxx_recipe = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
out = ROOT / 'build/native-adreno'
out.mkdir(parents=True, exist_ok=True)
sources = [ROOT / 'kits/sheng' / name for name in
           ('ShengGmu.c', 'ShengGmuVotes.c', 'ShengGmuCommandDb.c')]
sources += [ROOT / 'kits/AdrenoFirmware' / name for name in
            ('AdrenoHfi.c', 'AdrenoHfiStartup.c', 'AdrenoHfiVotes.c', 'AdrenoFirmware.c')]
sources += sorted(p for p in (ROOT / 'kits/ArmSmmu4K').glob('ArmSmmu4K*.c'))
sources += [ROOT / 'kits/AdrenoPM4/AdrenoPM4.c']
objects = []
# NativeAdrenoAccelerator.cpp needs the original IOGPUFamily (Metal stage); NativeAdrenoService.cpp is build-arm-driver.py's
# NativeAdreno.o and NativeAdrenoClient.cpp is not part of sheng's kernel archive either.
sources += sorted(p for p in (ROOT / 'drivers/NativeAdreno').glob('NativeAdreno*.cpp')
                  if p.name not in ('NativeAdrenoClient.cpp', 'NativeAdrenoService.cpp', 'NativeAdrenoAccelerator.cpp'))
for source in sources:
    recipe = c_recipe if source.suffix == '.c' else cxx_recipe
    obj = out / (source.stem + ('.kit.o' if source.suffix == '.c' else '.o'))
    command, i = [], 0
    while i < len(recipe['arguments']):
        value = recipe['arguments'][i]
        if value == recipe['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(obj)]
            i += 1
        elif value in ('-MF', '-MJ'):
            i += 1
        elif value == '-Xclang' and recipe['arguments'][i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MP'):
            command.append(value)
        i += 1
    # Xcode 16.4's clang has no -Wunnecessary-virtual-specifier; its suppression is the only flag dropped.
    command += ['-I', str(ROOT / 'kits/sheng'), '-I', str(ROOT / 'kits/AdrenoFirmware'),
                '-I', str(ROOT / 'build/sheng-gmu'), '-I', str(ROOT / 'kits/AdrenoPM4'), '-I', str(ROOT / 'kits/ArmSmmu4K'), '-I', str(ROOT / '.cache/xnu-work/osfmk'),
                '-I', str(ROOT / 'drivers/NativeAdreno'), '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-Wno-incompatible-sysroot', '-Wno-c99-designator',
                '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion']
    result = subprocess.run(command, cwd=recipe['directory'], capture_output=True, text=True)
    if result.returncode:
        sys.exit(str(source) + ':\n' + result.stderr[-2500:])
    objects.append(obj)
pins = json.loads((ROOT / 'scripts/x910-gpu-inputs.json').read_text())['firmware']['files']
local = ROOT / '.cache/firmware/sheng-gpu'
embedded = [('_native_adreno_firmware_image', 'qcom/gmu_gen70200.bin'),
            ('_native_adreno_sqe_firmware_image', 'qcom/a740_sqe.fw')]
assembly = ['.section __DATA_CONST,__const']
for label, key in embedded:
    path = local / key
    blob = path.read_bytes() if path.is_file() else b''
    if len(blob) != pins[key]['bytes'] or hashlib.sha256(blob).hexdigest() != pins[key]['sha256']:
        sys.exit('firmware %s is missing or does not match its pin' % key)
    assembly += ['.p2align 12', '.globl ' + label, label + ':', '.incbin ' + json.dumps(str(path)), label + '_end:',
                 '.p2align 3', '.globl ' + label + '_size', label + '_size:', '.quad ' + label + '_end-' + label]
(out / 'firmware.S').write_text('\n'.join(assembly) + '\n')
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Wno-incompatible-sysroot', '-c', out / 'firmware.S',
                '-o', out / 'firmware.o'], check=True)
objects.append(out / 'firmware.o')
archive = out / 'libNativeAdreno.a'
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
defined = subprocess.run(['xcrun', 'nm', '-g', archive], capture_output=True, text=True).stdout
missing = [s for s in ('_ShengGmuColdStart', '_ShengGmuBuildStartup',
                       '_ShengGmuCommandDbInputs', '_AdrenoFirmwarePlanGmu',
                       '_ArmSmmu4KAttachStreams', '_ArmSmmu4KBindMmio', '_AdrenoPM4Type7',
                       '_NativeAdrenoGmuContextBringup')
           if (' T ' + s) not in defined and (' S ' + s) not in defined]
if missing:
    sys.exit('Required kit definitions absent: ' + ', '.join(missing))
if '_native_adreno_zap_image' in defined:
    sys.exit('A zap shader was embedded')
(out / 'x910-adreno-report.json').write_text(json.dumps(dict(
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), objects=[p.name for p in objects],
    firmware={key: pins[key] for _, key in embedded}, zap_embedded=False, hardware_tested=False), indent=2) + '\n')
print('PASS: %d kit objects archived with all eight required definitions present, GMU+SQE firmware, no zap'
      % len(objects))
