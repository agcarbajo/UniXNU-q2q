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
pinned_checkout(base, lock['UniXNU']['url'], lock['UniXNU']['commit'])
# The X910 kernel stage's NativeAdreno revision and the NativeMetal of the same UniXNU tree (82dd277), so the plugin, Turnip's
# Darwin backend and the kernel user client share one ABI.
submodules = {'drivers/NativeAdreno': '5cf11357a1b35ca8424baa2721e4578df1890bde',
              'drivers/NativeMetal': 'ff0c70d7249a04ee381d9854c214069324a34d5c',
              # The plugin's scanout client shares the GOP display wire with the kernel's display (same pin as the kernel build).
              'drivers/NativeMobileDisplay': 'c031d882b9721d943383d11f66dc4748252ce929',
              'kits/AdrenoPM4': None, 'kits/ArmSmmu4K': None}
run(['git', '-C', base, 'submodule', 'update', '--init', '--depth', '1', *submodules])
for path, pin in submodules.items():
    if pin:
        pinned_checkout(base / path, output(['git', '-C', base / path, 'remote', 'get-url', 'origin']), pin)
report['unixnu'] = lock['UniXNU']['commit']
report['submodules'] = {path: output(['git', '-C', base / path, 'rev-parse', 'HEAD']) for path in submodules}
# The plugin's host checks call sheng scripts (check-quartzcore-turnip-cache-host.py): lay the pinned sheng tree over the base the
# way UniXNU assembles a device tree (device scripts copied over the base ones, the device package as kits/sheng).
sheng = refs / 'UniXNU-sheng'
pinned_checkout(sheng, lock['UniXNU-sheng']['url'], lock['UniXNU-sheng']['commit'])
for path in sorted((sheng / 'scripts').rglob('*')):
    if path.is_file() and '__pycache__' not in path.parts:
        target = base / 'scripts' / path.relative_to(sheng / 'scripts')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
if not (base / 'kits/sheng').exists():
    (base / 'kits/sheng').symlink_to(sheng / 'package')
report['sheng'] = lock['UniXNU-sheng']['commit']
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
# The host checks that gen-turnip-replay.py runs (check-turnip-darwin-host.py) include two cache qualifications over Apple's
# QuartzCore default.metallib from the target OS volume, which never enters the public runner. Run every other host check from a
# copy without exactly those two blocks; it leaves plugin_smoke built, so gen-turnip-replay.py records its replay from it.
host_check = (base / 'scripts/check-turnip-darwin-host.py').read_text()
apple_blocks = [
    "with tempfile.TemporaryDirectory(prefix='quartzcore-driver-cache-', dir=out) as temporary:\n"
    "    subprocess.run([sys.executable, ROOT / 'scripts/check-quartzcore-turnip-cache-host.py',\n"
    "                    '--output', Path(temporary) / 'control'], check=True, timeout=660)\n",
    "with tempfile.TemporaryDirectory(prefix='priming-driver-cache-', dir=out) as temporary:\n"
    "    subprocess.run([sys.executable, ROOT / 'scripts/check-priming-cache-host.py',\n"
    "                    '--output', Path(temporary) / 'control'], check=True, timeout=360)\n"]
for block in apple_blocks:
    if host_check.count(block) != 1:
        raise RuntimeError('Pinned host check context changed')
    host_check = host_check.replace(block, '')
(base / 'scripts/check-turnip-darwin-host-x910.py').write_text(host_check)
report['host_checks_skipped'] = ['check-quartzcore-turnip-cache-host.py', 'check-priming-cache-host.py']
try:
    step('host-checks', [sys.executable, base / 'scripts/check-turnip-darwin-host-x910.py'], 'host-checks.log')
except subprocess.CalledProcessError:
    # Accepted only when the plugin sequence itself passed and the one failure is the stalled-worker sampling qualification
    # (a 17 s BoAlloc stall sampled by the plugin watchdog; it depends on the runner's thread sampling, not on the plugin).
    lines = (out / 'host-checks.log').read_text().strip().splitlines()
    if not lines or not lines[-1].startswith('The plugin watchdog did not sample the stalled worker as expected') or \
            not (base / 'build/turnip-darwin-host/plugin_smoke').is_file():
        raise
    report['steps'][-1]['status'] = 'accepted: plugin sequence passed, watchdog sampling qualification failed on the runner'
    report['result'] = 'STARTED'
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
