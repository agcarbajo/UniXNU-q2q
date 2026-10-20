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
        run(['git','clone','--no-checkout','--filter=blob:none',url,target])
    elif output(['git','-C',target,'status','--porcelain']):
        raise RuntimeError('Refusing modified reference: '+str(target))
    run(['git','-C',target,'fetch','--depth','1','origin',commit])
    run(['git','-C',target,'checkout','--detach',commit])
    if output(['git','-C',target,'rev-parse','HEAD']) != commit: raise RuntimeError('Pin mismatch')
    return target

base = fetch('UniXNU',lock['UniXNU']['url'],lock['UniXNU']['commit'])
sheng = fetch('UniXNU-sheng',lock['UniXNU-sheng']['url'],lock['UniXNU-sheng']['commit'])
run(['git','-C',base,'submodule','update','--init','--depth','1',
     'drivers/ArmCpu','drivers/ArmDTPlatform','kits/NativePrelink'])
sys.path.insert(0,str(base/'scripts'))
from unixnu.cli import parser as upstream_parser
from unixnu.config import Context, load_toml, parse_config
from unixnu.feeds import Module
from unixnu import kernel, tree
from unixnu.util import Error, run as upstream_run

specs = {item['name']:item for item in load_toml(base/'feeds.toml')['source'] if item['kind']=='refer'}
needed = ('xnu','libplatform','libdispatch','AvailabilityVersions','corecrypto-2023')
modules = {}
for name in needed:
    entry = specs[name]
    path = fetch(name,entry['git'],entry['commit'])
    modules[('refer',name)] = Module('refer',name,path,'git',entry)
for name in ('ArmCpu','ArmDTPlatform'):
    modules[('driver',name)] = Module('driver',name,base/'drivers'/name,'base')
modules[('kit','NativePrelink')] = Module('kit','NativePrelink',base/'kits/NativePrelink','base')
modules[('patch','xnu')] = Module('patch','xnu',base/'patches/xnu','base')
modules[('package','main')] = Module('package','main',base/'package','base')
arguments = upstream_parser().parse_args(['build','kernel','-D',str(ROOT/'ci'),
                                         '--out',str(work/'out'),'-j',str(options.jobs)])
context = Context(arguments)
context.work.mkdir(parents=True,exist_ok=True)
shutil.copyfile(ROOT/'ci/configs/q2q_ci_defconfig',context.config_path)
context.config = parse_config(context.config_path.read_text())
tree.assemble(context,modules)
builder = sheng/'scripts/build-arm-driver.py'
shutil.copyfile(builder,context.tree/'scripts/build-arm-driver.py')
report = {'result':'PREPARED','compile_requested':not options.prepare_only,
          'target':'ARM64 / DEVELOPMENT / BCM2837 / iOS17 / 4KiB',
          'native_ufs':False,'gpu':False,'prelink':False,'ipsw_downloaded':False,
          'base_commit':lock['UniXNU']['commit'],'sheng_builder_commit':lock['UniXNU-sheng']['commit'],
          'arm_builder_sha256':hashlib.sha256(builder.read_bytes()).hexdigest(),
          'references':{name:specs[name]['commit'] for name in needed}}
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
