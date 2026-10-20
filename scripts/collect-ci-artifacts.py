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
