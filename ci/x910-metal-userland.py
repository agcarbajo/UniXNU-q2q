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
# Upstream's tree of 2026-10-30: its display wire is the qualified driver c031d88's, byte for byte, with BGRA framebuffers and the blit to the scanout.
UNIXNU_V1 = 'f0721e9745c45ad2600cd69c74730f1cca55d56a'
SHENG_V1 = 'd92e7996b9d407a83b8db486b7f633c651d8683c'
pinned_checkout(base, lock['UniXNU']['url'], UNIXNU_V1)
submodules = {'drivers/Adreno740': None, 'drivers/AdrenoMetal': None, 'drivers/GopFramebuffer': None,
              'kits/AdrenoPM4': None, 'kits/ArmSmmu4K': None, 'kits/AirNir': None, 'kits/AdrenoFirmware': None}
run(['git', '-C', base, 'submodule', 'update', '--init', '--depth', '1', *submodules])
# The drivers at this revision still include each other by their older directory names.
# The kernel's row for a client's first problem holds 63 characters: the reason, without the words every refusal starts with.
plugin_source = base / 'drivers/AdrenoMetal/NativeAdrenoMetalPlugin.mm'
plugin_text = plugin_source.read_text()
failure_line = 'snprintf(text, sizeof text, "%.14s %.46s", step, detail ? detail : "");'
if plugin_text.count(failure_line) != 1: raise RuntimeError('Pinned client failure line changed')
plugin_source.write_text(plugin_text.replace(failure_line, 'const char *why = detail ? detail : ""; for (const char *prefix : {"native-metal: ", "command buffer refused: "}) if (!strncmp(why, prefix, strlen(prefix))) why += strlen(prefix); snprintf(text, sizeof text, "%.3s %.58s", step, why);'))
for old, new in (('NativeAdreno', 'Adreno740'), ('NativeMetal', 'AdrenoMetal'), ('NativeMobileDisplay', 'GopFramebuffer')):
    if not (base / 'drivers' / old).exists():
        (base / 'drivers' / old).symlink_to(new)
# Widgets draw into RGB8_444_2P. Upstream's store of passes into the code formats (metal2vulkan 815e96f8), on the source the recorded patches leave.
patch_name = '0017-store-passes-into-code-formats.patch'
patch_bytes = (Path(__file__).resolve().parent / 'x910-code-format-store.patch').read_bytes()
(base / 'drivers/AdrenoMetal/bridge/patches' / patch_name).write_bytes(patch_bytes)
bridge_script = base / 'scripts/build-adreno-metal-bridge.py'
bridge_text = bridge_script.read_text()
last_patch = "'0016-tile-stores-within-rows.patch']]"
if bridge_text.count(last_patch) != 1: raise RuntimeError('Pinned bridge patch list changed')
bridge_script.write_text(bridge_text.replace(last_patch, "'0016-tile-stores-within-rows.patch', '%s']]" % patch_name))
# The trace writer retries its file every 400 ms; a sandboxed client is denied each time, and each denial is reported. One refusal ends it.
frontend = base / 'drivers/AdrenoMetal/NativeBridgeMetalFrontend.mm'
frontend_text = frontend.read_text()
retry = '   if(fd<0)for(const char *path:paths){fd=open(path,O_WRONLY|O_CREAT|O_TRUNC,0644);if(fd>=0)break;}\n   if(fd<0)continue;\n'
if frontend_text.count(retry) != 1: raise RuntimeError('Pinned trace writer changed')
frontend.write_text(frontend_text.replace(retry, retry.replace('continue;', 'return;')))
report['unixnu'] = UNIXNU_V1
report['submodules'] = {path: output(['git', '-C', base / path, 'rev-parse', 'HEAD']) for path in submodules}
# The plugin's host checks call sheng scripts (check-quartzcore-turnip-cache-host.py): lay the pinned sheng tree over the base the
# way UniXNU assembles a device tree (device scripts copied over the base ones, the device package as kits/sheng).
sheng = refs / 'UniXNU-sheng'
pinned_checkout(sheng, lock['UniXNU-sheng']['url'], SHENG_V1)
if not (base / 'kits/sheng').exists():
    (base / 'kits/sheng').symlink_to(sheng / 'package')
report['sheng'] = SHENG_V1
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
# The host simulator of this tree does not build its driver sources on the runner, so its checks do not run. The prefix replay they feed is an optional diagnostic
# of the plugin (__has_include("TurnipReplay.h")).
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
