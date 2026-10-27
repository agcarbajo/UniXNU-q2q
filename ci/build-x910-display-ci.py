"""Compile the pinned GOP display; bind only locally audited original ABI slots.

No Apple image or board MMIO inputs are used on this runner.
"""
import hashlib,json,os,shutil,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'kits/NativePrelink'))
from native_prelink import macho
OUT=ROOT/'build/native-legacy-display';OUT.mkdir(parents=True,exist_ok=True)
def run(args,**kwargs):subprocess.run([str(x) for x in args],check=True,**kwargs)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(c,why):
    if not c:raise RuntimeError(why)
abi=json.loads((ROOT/'scripts/x910-display-abi.json').read_text())
shutil.copyfile(ROOT/'scripts/OriginalLegacyFramebufferABI.h',OUT/'OriginalLegacyFramebufferABI.h')
run([sys.executable,ROOT/'scripts/build-arm-driver.py','NativeLegacyDisplay'])
obj=OUT/'NativeLegacyDisplay.o';raw=obj.read_bytes();image=macho(raw)
at=next(p for c,p,_ in image['commands'] if c==2)
sy,count,st,_=struct.unpack_from('<4I',raw,at+8);symbols=[]
for i in range(count):
    ix,kind,_,_,va=struct.unpack_from('<IBBHQ',raw,sy+i*16)
    symbols.append((raw[st+ix:raw.index(b'\0',st+ix)].decode(),va,kind))
vtable=next(v for n,v,k in symbols if n=='__ZTV19NativeLegacyDisplay');relocations={}
for segment in image['segments']:
    for sec in segment['sections']:
        if sec['va']<=vtable<sec['va']+sec['size']:
            ro,rc=struct.unpack_from('<II',raw,sec['command_offset']+56)
            for i in range(rc):
                offset,bits=struct.unpack_from('<II',raw,ro+i*8)
                if bits>>27&1 and bits>>28==0 and bits>>25&3==3:
                    relocations[sec['va']+offset]=symbols[bits&0xffffff][0]
slots=[relocations.get(vtable+16+i*8) for i in range(358)]
require(all(slots) and vtable+16+358*8 not in relocations,'Exact 358-slot subclass')
bindings={n:int(v,16) for n,v in abi['bindings'].items()};owned=[]
for i,name in enumerate(slots):
    if name.startswith(('__ZN19NativeLegacyDisplay','__ZNK19NativeLegacyDisplay')):
        owned.append(i)
    elif '25IOMobileFramebufferLegacy' in name:
        require(i not in abi['abstract_slots'],'No abstract original import')
        address=int(abi['methods'][i]['address'],16)
        require(name not in bindings or bindings[name]==address,'Consistent original alias')
        bindings[name]=address
    else:
        require(i<168 and name in abi['native_base_names'][str(i)],'Native base slot '+str(i))
expected=sorted([0,1,7,14,84,86,87,141,155,184,188,288,290,296,301]+abi['abstract_slots'])
require(owned==expected,'Qualified subclass override set')
original_imports={n for n,v,k in symbols if k==1 and 'IOMobileFramebufferLegacy' in n}
require(original_imports=={n for n in bindings if 'IOMobileFramebufferLegacy' in n},'Complete original base imports')
(OUT/'original-bindings.S').write_text(''.join(f'.globl {n}\n.set {n}, {v:#x}\n' for n,v in sorted(bindings.items())))
run(['xcrun','clang','-target','arm64-apple-ios17.0','-Werror','-Wno-incompatible-sysroot','-c',OUT/'original-bindings.S','-o',OUT/'original-bindings.o'])
llvm=Path(subprocess.check_output(['brew','--prefix','llvm@18'],text=True).strip())/'bin'
objects=[OUT/'original-bindings.o']
for component,folder in [('NativeLegacyDisplay','native-legacy-display'),('NativeGOPPresenter','native-gop-presenter')]:
    if component!='NativeLegacyDisplay':run([sys.executable,ROOT/'scripts/build-arm-driver.py',component])
    source=ROOT/'build'/folder/(component+'.o');late=OUT/(component+'.late.o')
    run([llvm/'llvm-objcopy','--remove-section=__DATA,__mod_init_func','--globalize-symbol=__GLOBAL__sub_I_'+component+'.cpp',source,late])
    def text(p):
        b=p.read_bytes();m=macho(b);s=next(s for g in m['segments'] for s in g['sections'] if s['name']=='__text');return b[s['fileoff']:s['fileoff']+s['size']]
    require(text(source)==text(late),'Owned instructions preserved during init staging')
    objects.append(late)
recipe=ROOT/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
r=json.loads(recipe.read_text().strip().rstrip(','));args=r['arguments'];cmd=[];i=0
source=ROOT/'kits/sheng/X910LegacyDisplayProbe.cpp';destination=OUT/'X910LegacyDisplayProbe.o'
while i<len(args):
    value=args[i]
    if value==r['file']:cmd.append(str(source))
    elif value=='-o':cmd += ['-o',str(destination)];i+=1
    elif value in ('-MF','-MJ','-MT'):i+=1
    elif value=='-Xclang' and args[i+1].startswith('-W'):i+=1
    elif value not in ('-MD','-MP') and not value.startswith('-W'):cmd.append(value)
    i+=1
cmd+=['-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-c99-designator','-Wno-reorder-init-list','-Wno-deprecated-enum-enum-conversion','-I',str(ROOT/'.cache/xnu-work/osfmk')]
for i,v in enumerate(cmd):
    folder=cmd[i+1] if v in ('-I','-isystem','-iquote') else v[2:] if v.startswith('-I') else None
    if folder:Path(r['directory'],folder).mkdir(parents=True,exist_ok=True)
run(cmd,cwd=r['directory']);objects.append(destination)
archive=OUT/'libNativeLegacyDisplay.late.a';archive.unlink(missing_ok=True)
run(['xcrun','ar','rcs',archive,*objects])
sources=[source,ROOT/'drivers/NativeMobileDisplay/NativeLegacyDisplay.cpp',ROOT/'drivers/NativeMobileDisplay/NativeLegacyRequest.h',ROOT/'drivers/NativeMobileDisplay/NativeGOPPresenter.cpp',ROOT/'drivers/NativeMobileDisplay/NativeGOPWire.h']
(OUT/'build-report.json').write_text(json.dumps(dict(archive_sha256=sha(archive),source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},
    abi_sha256=sha(ROOT/'scripts/x910-display-abi.json'),virtual_entries=358,owned_entries=owned,bindings={n:hex(v) for n,v in bindings.items()},
    original_instructions_modified=False,xiaomi_hardware_probes=False,client_vptr_interception=False,hardware_tested=False),indent=2)+'\n')
print('X910 GOP display compiled and slot-qualified; runtime not yet tested')
