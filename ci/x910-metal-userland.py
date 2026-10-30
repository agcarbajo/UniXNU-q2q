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
              'kits/AdrenoPM4': None, 'kits/ArmSmmu4K': None}
run(['git', '-C', base, 'submodule', 'update', '--init', '--depth', '1', *submodules])
for path, pin in submodules.items():
    if pin:
        pinned_checkout(base / path, output(['git', '-C', base / path, 'remote', 'get-url', 'origin']), pin)
report['unixnu'] = lock['UniXNU']['commit']
report['submodules'] = {path: output(['git', '-C', base / path, 'rev-parse', 'HEAD']) for path in submodules}
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
