"""Collect only source-build reports/logs/kernel, excluding SDK and Apple firmware."""
import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work',type=Path,required=True)
options = parser.parse_args()
work = options.work.resolve()
artifact = work/'artifacts'
artifact.mkdir(parents=True,exist_ok=True)
logs = work/'out/q2q-source-ci/logs'
if logs.is_dir():
    shutil.copytree(logs,artifact/'logs',dirs_exist_ok=True)
transcript = Path('ci-build.log')
if transcript.is_file(): shutil.copyfile(transcript,artifact/'ci-build.log')
for report in (work/'out/q2q-source-ci/tree/build').rglob('*report*.json'):
    relative = report.relative_to(work/'out/q2q-source-ci/tree/build')
    target = artifact/'component-reports'/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(report,target)
print('Collected source kernel, reports and logs only:',artifact)

# Preserve source-derived PMGR objects for independent local ABI/slot checks.
pmgr=work/'out/q2q-source-ci/tree/build/sheng-platform-power'
for name in ('ShengPlatformPower.o','ShengPlatformPower.late.o','original-bindings.o','OriginalPMGRABI.h','GuardedPMGRSlots.inc'):
    source=pmgr/name
    if source.is_file():
        dest=artifact/'pmgr-objects'/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest)

# Source AES objects for local ABI checks against the pinned original module.
aes=work/"out/q2q-source-ci/tree/build/native-aes"
for source in aes.glob("*"):
    if source.suffix in (".o", ".S", ".json"):
        dest=artifact/"aes-objects"/source.name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest)

# X910 NativeUfs object/imports and the VerifiedRoot carrier, for the local storage-binding and policy steps.
for folder,label in (('native-ufs','native-ufs-objects'),('x910-verified-root','x910-verified-root'),('native-ram-block','native-ram-block-objects'),('native-legacy-display','display-objects')):
    for source in (work/'out/q2q-source-ci/tree/build'/folder).glob('*'):
        if source.suffix in ('.o','.S','.json','.a','.log'):
            dest=artifact/label/source.name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,dest)
