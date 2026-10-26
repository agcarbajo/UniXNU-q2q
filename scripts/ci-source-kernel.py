"""Assemble a pinned source-only XNU build on a PC or macOS runner.

--prepare-only is supported on Linux. Compilation requires macOS/Xcode.
Does not download an IPSW, prelink Apple binaries, use ADB or install anything.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work',type=Path,required=True)
parser.add_argument('--prepare-only',action='store_true')
parser.add_argument('--jobs',type=int,default=2)
options = parser.parse_args()
work = options.work.expanduser().resolve()
if ' ' in str(work): raise SystemExit('Use a build work directory without spaces')
refs = work/'refs'
refs.mkdir(parents=True,exist_ok=True)
lock = {item['name']:item for item in json.loads((ROOT/'sources.lock.json').read_text())}

def run(command, **kwargs):
    print('+ '+' '.join(map(str,command)),flush=True)
    return subprocess.run(list(map(str,command)),check=True,**kwargs)

def output(command): return subprocess.check_output(list(map(str,command)),text=True).strip()

def fetch(name,url,commit):
    target = refs/name
    new = not target.exists()
    if new:
        # Upstream clones these refs with --shared. A partial promisor clone
        # cannot serve lazy blobs to that local upload-pack operation.
        run(['git','init',target])
        run(['git','-C',target,'remote','add','origin',url])
    elif output(['git','-C',target,'status','--porcelain']):
        raise RuntimeError('Refusing modified reference: '+str(target))
    run(['git','-C',target,'fetch','--depth','1','origin',commit])
    run(['git','-C',target,'checkout','--detach',commit])
    if output(['git','-C',target,'rev-parse','HEAD']) != commit: raise RuntimeError('Pin mismatch')
    return target

base = fetch('UniXNU',lock['UniXNU']['url'],lock['UniXNU']['commit'])
sheng = fetch('UniXNU-sheng',lock['UniXNU-sheng']['url'],lock['UniXNU-sheng']['commit'])
run(['git','-C',base,'submodule','update','--init','--depth','1',
     'drivers/ArmCpu','drivers/ArmDTPlatform','drivers/NativeAES','kits/NativePrelink',
     'drivers/NativeUfs','drivers/NativeVirtioBlock','kits/VerifiedRoot'])
sys.path.insert(0,str(base/'scripts'))
from unixnu.cli import parser as upstream_parser
from unixnu.config import Context, load_toml, parse_config
from unixnu.feeds import Module
from unixnu import kernel, tree
from unixnu.util import Error, run as upstream_run

specs = {item['name']:item for item in load_toml(base/'feeds.toml')['source'] if item['kind']=='refer'}
needed = ('xnu','libplatform','libdispatch','AvailabilityVersions','corecrypto-2023','libpthread')
modules = {}
for name in needed:
    entry = specs[name]
    path = fetch(name,entry['git'],entry['commit'])
    modules[('refer',name)] = Module('refer',name,path,'git',entry)
for name in ('ArmCpu','ArmDTPlatform','NativeAES','NativeVirtioBlock'):
    modules[('driver',name)] = Module('driver',name,base/'drivers'/name,'base')
# X910: sheng's NativeUfs selects its root partition by the literal GPT name "userdata", which on the
# Galaxy Tab S9 Ultra is Android's data partition. Build a copy that selects the dedicated ipadosroot
# partition instead; nothing else in the driver changes.
ufs = work/'x910-NativeUfs'
if ufs.exists(): shutil.rmtree(ufs)
shutil.copytree(base/'drivers/NativeUfs',ufs,ignore=shutil.ignore_patterns('.git'))
layout_text = (ufs/'RootLayout.h').read_text()
if layout_text.count('static const char wanted[] = "userdata";') != 1:
    raise RuntimeError('Pinned NativeUfs partition selector changed')
(ufs/'RootLayout.h').write_text(layout_text.replace('static const char wanted[] = "userdata";','static const char wanted[] = "ipadosroot";'))
run([sys.executable,ROOT/'ci/x910-native-ufs-firmware-bypass.py',ufs/'SmmuTakeover.h'])
run([sys.executable,ROOT/'ci/x910-native-ufs-link.py',ufs/'NativeUfs.cpp'])
run([sys.executable,ROOT/'ci/x910-native-ufs-low-dma.py',ufs/'NativeUfs.cpp'])
run([sys.executable,ROOT/'ci/x910-native-ufs-utrd.py',ufs])
run([sys.executable,ROOT/'ci/x910-native-ufs-wake.py',ufs/'NativeUfs.cpp'])
modules[('driver','NativeUfs')] = Module('driver','NativeUfs',ufs,'base')
storage = fetch('IOStorageFamily','https://github.com/apple-oss-distributions/IOStorageFamily.git','e45fbe30a7879e0b16e26dddb78538f1b00e0bff')
modules[('refer','IOStorageFamily')] = Module('refer','IOStorageFamily',storage,'git',{'git':'https://github.com/apple-oss-distributions/IOStorageFamily.git','commit':'e45fbe30a7879e0b16e26dddb78538f1b00e0bff'})
modules[('kit','VerifiedRoot')] = Module('kit','VerifiedRoot',base/'kits/VerifiedRoot','base')
modules[('kit','NativePrelink')] = Module('kit','NativePrelink',base/'kits/NativePrelink','base')
modules[('patch','xnu')] = Module('patch','xnu',base/'patches/xnu','base')
modules[('patch','libpthread')] = Module('patch','libpthread',base/'patches/libpthread','base')
modules[('package','main')] = Module('package','main',base/'package','base')
arguments = upstream_parser().parse_args(['build','kernel','-D',str(ROOT/'ci'),
                                         '--out',str(work/'out'),'-j',str(options.jobs)])
context = Context(arguments)
context.work.mkdir(parents=True,exist_ok=True)
shutil.copyfile(ROOT/'ci/configs/q2q_ci_defconfig',context.config_path)
context.config = parse_config(context.config_path.read_text())
tree.assemble(context,modules)
builder = sheng/'scripts/build-arm-driver.py'
builder_text = builder.read_text()
unsupported_warning = "'-Wno-unnecessary-virtual-specifier', "
if builder_text.count(unsupported_warning) != 1:
    raise RuntimeError('Pinned driver warning option context changed')
# Apple clang in Xcode16.4 does not implement this later warning. Keep -Werror
# and every other warning check; remove only that unsupported suppression.
builder_text = builder_text.replace(unsupported_warning,'')
(context.tree/'scripts/build-arm-driver.py').write_text(builder_text)
if True:  # Files are only compiled when the board archive/config opts in.
    # Compile the pinned original sheng source; audit original bindings after local prelink.
    (context.tree/'kits/sheng').mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'ci/ShengPlatformPower.cpp',context.tree/'kits/sheng/ShengPlatformPower.cpp')
    shutil.copyfile(ROOT/'ci/build-sheng-platform-power-ci.py',context.tree/'scripts/build-sheng-platform-power-ci.py')
    shutil.copyfile(ROOT/'ci/build-x910-native-ufs-ci.py',context.tree/'scripts/build-x910-native-ufs-ci.py')
    shutil.copyfile(ROOT/'ci/X910RootAuthority.cpp',context.tree/'scripts/X910RootAuthority.cpp')
    if (ROOT/'ci/x910-storage-bindings.json').is_file():
        shutil.copyfile(ROOT/'ci/x910-storage-bindings.json',context.tree/'scripts/x910-storage-bindings.json')

# The base offline graphics probe calls a legacy-display provider even when
# that provider is not selected. Guard that optional call by its real build
# flag, as the same patch already does for its other call site. Never add a
# fake display implementation merely to satisfy the link.
patches = work/'q2q-patches/xnu'
shutil.copytree(base/'patches/xnu',patches,dirs_exist_ok=True)
probe_patch = patches/'offline-component-load-probes.patch'
patch_text = probe_patch.read_text()
legacy_start = '+\tint legacy_register = 0;'
legacy_end = '+\tint client_probe = 0;'
if patch_text.count(legacy_start) != 1 or patch_text.count(legacy_end) != 1:
    raise RuntimeError('Pinned optional display probe context changed')
patch_text = patch_text.replace('@@ -478,5 +478,88 @@','@@ -478,5 +478,90 @@')
patch_text = patch_text.replace(legacy_start,'+#if defined(XNU_NATIVE_LEGACY_DISPLAY) && XNU_NATIVE_LEGACY_DISPLAY\n'+legacy_start)
patch_text = patch_text.replace(legacy_end,'+#endif\n'+legacy_end)
probe_patch.write_text(patch_text)
# X910: the owner-root gates require sheng's virtio verified block and its boot-time crypto probe. On the X910 the
# verified block is NativeUfs (every root read is checked against the owner-signed manifest) and the
# root-authority queries are carried by ci/X910RootAuthority.cpp; accept that pairing only when XNU_X910_UFS_ROOT=1.
baseline = (context.tree/'scripts/build-xnu-4k-baseline.sh').resolve()
baseline_text = baseline.read_text()
for old_gate, new_gate in (
    ('if [ "$OWNER_ROOT_AUDIT" = 1 ] && [ "$OWNER_ROOT_PROBE" != 1 ]; then',
     'if [ "$OWNER_ROOT_AUDIT" = 1 ] && [ "$OWNER_ROOT_PROBE" != 1 ] && [ "${XNU_X910_UFS_ROOT:-0}" != 1 ]; then'),
    ('if [ "$OWNER_ROOT_TRUST" = 1 ] && { [ "$OWNER_ROOT_AUDIT" != 1 ] || [ "${XNU_NATIVE_VIRTIO_BLOCK:-0}" != 1 ]; }; then',
     'if [ "$OWNER_ROOT_TRUST" = 1 ] && { [ "$OWNER_ROOT_AUDIT" != 1 ] || { [ "${XNU_NATIVE_VIRTIO_BLOCK:-0}" != 1 ] && { [ "${XNU_X910_UFS_ROOT:-0}" != 1 ] || [ "${XNU_NATIVE_UFS:-0}" != 1 ]; }; }; }; then')):
    if baseline_text.count(old_gate) != 1:
        raise RuntimeError('Pinned owner-root gate changed')
    baseline_text = baseline_text.replace(old_gate, new_gate)
baseline.write_text(baseline_text)
(context.tree/'patches/xnu').unlink()
(context.tree/'patches/xnu').symlink_to(patches)
# prepare-xnu-source.py normally lists these public headers through the
# unauthenticated GitHub contents API. Supply its cache from pinned Git trees
# instead, retaining the upstream Git-blob verification for every restored file.
header_pins = {
    'xnu-10063.121.3':'2c2f96dc2b9a4408a43d3150ae9c105355ca3daa',
    'xnu-8796.101.5':'aca3beaa3dfbd42498b42c5e5ce20a938e6554e5',
}
versions = kernel.version_env(modules)
header_cache = context.tree/'.cache/sources'
header_cache.mkdir(parents=True,exist_ok=True)
for variable,directory,prefix in (
    ('UNIXNU_DRIVERKIT_TAG','iokit/DriverKit','xnu-driverkit-'),
    ('UNIXNU_DRIVERKIT_TAG','iokit/DriverKit/crypto','xnu-driverkit-crypto-'),
    ('UNIXNU_TRUSTCACHE_TAG','EXTERNAL_HEADERS/TrustCache','xnu-trustcache-'),
):
    tag = versions[variable]
    reference = refs/('headers-'+tag)
    if not reference.is_dir():
        reference = fetch('headers-'+tag, specs['xnu']['git'], header_pins[tag])
    if output(['git','-C',reference,'rev-parse','HEAD']) != header_pins[tag]:
        raise RuntimeError('Header reference pin mismatch')
    entries = []
    for line in output(['git','-C',reference,'ls-tree','HEAD',directory+'/']).splitlines():
        metadata,path = line.split('\t',1)
        mode,kind,blob = metadata.split()
        if kind != 'blob' or not path.endswith('.h'): continue
        entries.append({'type':'file','name':Path(path).name,'sha':blob,
                        'download_url':(reference/path).as_uri()})
    if not entries: raise RuntimeError('Missing public header tree: '+directory)
    (header_cache/(prefix+tag.removeprefix('xnu-')+'.json')).write_text(json.dumps(entries)+'\n')
report = {'result':'PREPARED','compile_requested':not options.prepare_only,
          'target':'ARM64 / DEVELOPMENT / BCM2837 / iOS17 / 4KiB',
          'native_ufs':True,'native_ufs_partition':'ipadosroot','gpu':False,'prelink':False,'ipsw_downloaded':False,
          'base_commit':lock['UniXNU']['commit'],'sheng_builder_commit':lock['UniXNU-sheng']['commit'],
          'arm_builder_sha256':hashlib.sha256(builder.read_bytes()).hexdigest(),
          'effective_arm_builder_sha256':hashlib.sha256(builder_text.encode()).hexdigest(),
          'optional_display_probe_patch_sha256':hashlib.sha256(patch_text.encode()).hexdigest(),
          'references':{name:specs[name]['commit'] for name in needed},
          'public_header_references':header_pins}
artifact = work/'artifacts'
artifact.mkdir(exist_ok=True)
(artifact/'source-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
if options.prepare_only:
    print('Pinned source-only build tree prepared; no compiler invoked')
    raise SystemExit(0)
if platform.system() != 'Darwin':
    raise SystemExit('Compilation requires macOS: upstream uses xcodebuild, xcrun and IIG')
for tool in ('xcodebuild','xcrun','cmake','ninja','unifdef'):
    if not shutil.which(tool): raise SystemExit('Missing build tool: '+tool)
os.environ['JOBS'] = str(options.jobs)
report['xcode'] = output(['xcodebuild','-version'])
report['iphoneos_sdk'] = output(['xcrun','--sdk','iphoneos','--show-sdk-version'])
# Invoke only the source build, deliberately omitting kernel.prelink and all image steps.
try:
    # The upstream bootstrap retains flags for archives that do not exist yet;
    # its shell script tests those archives before reaching make. Generate the
    # compile records first with optional providers disabled, then build the
    # real archives and let kernel.build perform the final source link.
    obj = context.tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    record = obj/'osfmk/DEVELOPMENT/startup.o.json'
    filelist = obj/'link.filelist'
    if not record.is_file() or not filelist.is_file():
        bootstrap_env = dict(os.environ)
        bootstrap_env.update(kernel.version_env(modules))
        bootstrap_env.update({key:'0' for key in kernel.xnu_env(context) if key != 'XNU_CRYPTO_BOOTSTRAP'})
        bootstrap_env['XNU_CRYPTO_BOOTSTRAP'] = 'source'
        bootstrap_env['JOBS'] = str(options.jobs)
        try:
            upstream_run(['bash',context.tree/'scripts/build-xnu-4k-baseline.sh'],
                         cwd=context.tree,env=bootstrap_env,
                         log_file=context.logs/'source-records-bootstrap.log')
        except Error:
            if not record.is_file() or not filelist.is_file(): raise
    kernel.build(context,modules)
except Exception as error:
    report.update({'result':'FAILED','error':str(error),'hardware_tested':False})
    (artifact/'source-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    raise
candidate = context.tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'
data = candidate.read_bytes()
if data[:4] != b'\xcf\xfa\xed\xfe': raise RuntimeError('Kernel is not Mach-O 64')
cpu,subtype = struct.unpack_from('<II',data,4)
if cpu != 0x100000c or subtype & 0xffffff == 2: raise RuntimeError('Wrong kernel architecture')
shutil.copyfile(candidate,artifact/'kernel.source.arm64.macho')
report.update({'result':'BUILT','kernel_sha256':hashlib.sha256(data).hexdigest(),
               'kernel_bytes':len(data),'hardware_tested':False})
(artifact/'source-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Source ARM64 kernel built and architecture checked; not prelinked or hardware-tested')
