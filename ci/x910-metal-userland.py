"""Build the owner Adreno Metal userland of the GPU stage on the macOS runner: Turnip (Mesa freedreno Vulkan with the Darwin
kernel backend) for the host and for iOS 17, the native Metal bridge (metal2vulkan) and UniXNU's NativeAdrenoMetal.bundle plus the
adrenoprobed launchd probe, each ad-hoc signed with 4 KiB pages (scripts/build-adreno-metal-plugin.py, unchanged).

Runs UniXNU's own scripts in the pinned UniXNU tree, with NativeAdreno/NativeMetal at the revisions the X910 kernel stage uses.
Mesa, the translator and the toolchains are fetched at their recorded pins. Output in <work>/artifacts/metal-userland: the bundle,
the daemon, its job plists and trust.json (the three CDHashes that tools/make_x910_owner_trust.py puts in the owner module).
Nothing is installed; no Apple binary is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work', type=Path, required=True)
options = parser.parse_args()
work = options.work.resolve()
refs = work / 'refs'
refs.mkdir(parents=True, exist_ok=True)
artifacts = work / 'artifacts'
out = artifacts / 'metal-userland'
out.mkdir(parents=True, exist_ok=True)
lock = {item['name']: item for item in json.loads((ROOT / 'sources.lock.json').read_text())}
report = {'result': 'STARTED', 'steps': []}


def save():
    (out / 'metal-userland-report.json').write_text(json.dumps(report, indent=2) + '\n')


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def output(command):
    return subprocess.check_output(list(map(str, command)), text=True).strip()


def step(name, command, log=None, **kwargs):
    report['steps'].append({'name': name, 'status': 'running'})
    save()
    try:
        if log:
            with open(out / log, 'w') as stream:
                run(command, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
        else:
            run(command, **kwargs)
    except subprocess.CalledProcessError:
        report['steps'][-1]['status'] = 'failed'
        report['result'] = 'FAILED at ' + name
        save()
        if log:
            print((out / log).read_text()[-6000:])
        raise
    report['steps'][-1]['status'] = 'ok'
    save()


def pinned_checkout(target, url, commit):
    if not target.exists():
        run(['git', 'init', '-q', target])
        run(['git', '-C', target, 'remote', 'add', 'origin', url])
    run(['git', '-C', target, 'fetch', '-q', '--depth', '1', 'origin', commit])
    run(['git', '-C', target, 'checkout', '-q', '--detach', commit])
    if output(['git', '-C', target, 'rev-parse', 'HEAD']) != commit:
        raise RuntimeError('Pin mismatch: ' + str(target))


base = refs / 'UniXNU'
# Upgrade branch: upstream UniXNU's own tree, whose Mesa patches, translator pin and scripts match these drivers (components.toml, 2026-11-01).
UNIXNU_HEAD = '04b4e558bcded5213c2d802984862dc062f28257'
SHENG_HEAD = '28b30285768e04008ac525c04182e245bfac441e'
pinned_checkout(base, lock['UniXNU']['url'], UNIXNU_HEAD)
submodules = {'drivers/Adreno740': 'a9a2cbb43b89d672e2921e19721a6438b738b17f',
              'drivers/AdrenoMetal': 'a0f0287be80a5d8aef5c6b97a6cab62129e152bd',
              'drivers/GopFramebuffer': '19ef3a75ad2c53da85b8d923553d922716fce377',
              'kits/AdrenoPM4': '282606b528b2595bc4274ea7490b62b323f35a5f', 'kits/ArmSmmu4K': '83969cd874503e65997ff7639f7dcf3d0a92cd17',
              'kits/AirNir': 'b356371a74cd158c2902f4886f373ebb0f833a36', 'kits/AdrenoFirmware': '20ad3d7aca14bd46aecab6335c780f5413fa054b'}
run(['git', '-C', base, 'submodule', 'update', '--init', '--depth', '1', *submodules])
for path, pin in submodules.items():
    if pin:
        pinned_checkout(base / path, output(['git', '-C', base / path, 'remote', 'get-url', 'origin']), pin)
report['unixnu'] = UNIXNU_HEAD
# The host simulator builds the driver's sources without IOKit, and its fixture has no IOFree yet at this revision. Only the host check's copy is affected: this file is not shipped.
client_source = base / 'drivers/Adreno740/NativeAdrenoUserClient.cpp'
client_text = client_source.read_text()
role_line = next(line for line in client_text.splitlines() if line.startswith('#include <sys/resource_private.h>'))
client_source.write_text(client_text.replace(role_line, '#define PRIO_DARWIN_GPU_BACKGROUND (-2)\n#define PRIO_DARWIN_GPU_UTILITY (-3)\n#define task_get_gpu_role(task) (0)'))
submit_source = base / 'drivers/Adreno740/NativeAdrenoSubmit.cpp'
submit_source.write_text('#if !defined(KERNEL)\n#include <stdlib.h>\n#define IOFree(p,n) ::free(p)\n#endif\n' + submit_source.read_text()
                         + '\n#if !defined(KERNEL)\n#include "NativeAdrenoPreempt.cpp"\nextern "C" uint64_t native_adreno_now_us(void) {return 0;}\n#endif\n')
report['submodules'] = {path: output(['git', '-C', base / path, 'rev-parse', 'HEAD']) for path in submodules}
# The plugin's host checks call sheng scripts (check-quartzcore-turnip-cache-host.py): lay the pinned sheng tree over the base the
# way UniXNU assembles a device tree (device scripts copied over the base ones, the device package as kits/sheng).
sheng = refs / 'UniXNU-sheng'
pinned_checkout(sheng, lock['UniXNU-sheng']['url'], SHENG_HEAD)
# The overlay is only for the host checks this branch does not run; its older data files would replace the translator pin.
if not (base / 'kits/sheng').exists():
    (base / 'kits/sheng').symlink_to(sheng / 'package')
report['sheng'] = SHENG_HEAD
save()

# Host tools. glslang/shaderc/spirv-tools: Vulkan runtime and smoke shaders; vulkan-headers: host smoke; llvm: llvm-as for the
# translator fixtures; bison/flex: Mesa's parsers.
step('brew', ['brew', 'install', 'bison', 'flex', 'glslang', 'shaderc', 'spirv-tools', 'vulkan-headers', 'llvm', 'pkgconf'],
     'brew.log')
prefix = lambda formula: output(['brew', '--prefix', formula])
tools = base / '.cache/mesa-tools/bin'
tools.mkdir(parents=True, exist_ok=True)
for formula, names in (('bison', ('bison', 'yacc')), ('flex', ('flex',)), ('llvm', ('llvm-as',))):
    for name in names:
        link = tools / name
        if not link.exists():
            link.symlink_to(Path(prefix(formula)) / 'bin' / name)
os.environ['PATH'] = str(tools) + ':' + os.environ['PATH']
venv = base / '.cache/mesa-venv'
step('venv', [sys.executable, '-m', 'venv', venv])
step('pip', [venv / 'bin/pip', 'install', '-q', 'meson==1.9.1', 'Mako==1.3.10', 'MarkupSafe==3.0.4', 'packaging==25.0',
             'PyYAML==6.0.3', 'ply==3.11'], 'pip.log')

# Mesa at the compiler pin with UniXNU's recorded patches, in their recorded order.
compiler = json.loads((base / 'scripts/data/adreno-compiler-inputs.json').read_text())
mesa = base / '.cache/mesa-work'
pinned_checkout(mesa, compiler['mesa']['repository'] + '.git', compiler['mesa']['commit'])
for patch in compiler['patches']:
    run(['git', '-C', mesa, 'apply', base / patch])
report['mesa'] = compiler['mesa']['commit']
save()

step('turnip-host', [sys.executable, base / 'scripts/build-turnip-darwin.py', '--target', 'host'], 'turnip-host.log')
step('turnip-ios', [sys.executable, base / 'scripts/build-turnip-darwin.py', '--target', 'ios'], 'turnip-ios.log')

# The translator at its recorded pin, built by the pinned Rust toolchain the bridge asks for.
translator = json.loads((base / 'scripts/data/gpu-metal-vulkan-inputs.json').read_text())['translator']
pinned_checkout(base / '.cache/gpu-reference/metal2vulkan', translator['repository'] + '.git', translator['commit'])
step('rust', ['rustup', 'toolchain', 'install', '1.98.0', '--profile', 'minimal', '--target', 'aarch64-apple-ios'], 'rust.log')
# The host simulator at this revision does not reach a device (BO init fails on the runner), so its checks do not run on this branch. The prefix replay
# they feed is an optional diagnostic of the plugin (__has_include("TurnipReplay.h")).
(base / 'scripts/gen-turnip-replay.py').write_text("print('X910: no replay, host simulator not run')\n")
report['host_checks_skipped'] = ['check-turnip-darwin-host.py']
save()
step('plugin', [sys.executable, base / 'scripts/build-adreno-metal-plugin.py'], 'plugin.log')

built = base / 'build/adreno-metal-plugin'
for name in ('NativeAdrenoMetal.bundle',):
    shutil.copytree(built / name, out / name, symlinks=True, dirs_exist_ok=True)
for name in ('adrenoprobed', 'trust.json', 'org.blu-sharky.adrenoprobed.plist', 'com.apple.iomfb_fdr_loader.plist',
             'com.apple.bluetoothd.plist', 'adrenoprobed-entitlements.plist'):
    shutil.copyfile(built / name, out / name)
files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'metal-userland-report.json')
report.update(result='BUILT', trust=json.loads((out / 'trust.json').read_text()),
              files={str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, hardware_tested=False)
save()
print('Owner Adreno Metal userland built and 4K-signed; not installed or hardware-tested')
