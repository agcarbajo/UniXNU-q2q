"""Build/sign the owner GLES diagnostic on a macOS host with Xcode. Never deploys it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('A macOS/Xcode builder is required; no binary was built or deployed')
    args.output.mkdir(parents=True, exist_ok=True)
    source = ROOT/'experiments/game-support/gles_probe.mm'
    binary = args.output/'x910-gles-probe'
    command = ['xcrun', '--sdk', 'iphoneos', 'clang++', '-arch', 'arm64', '-miphoneos-version-min=12.0',
               '-std=c++17', '-fobjc-arc', '-O2', '-Wno-deprecated-declarations', str(source),
               '-framework', 'Foundation', '-framework', 'OpenGLES', '-framework', 'Metal', '-o', str(binary)]
    subprocess.run(command, check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', '--pagesize', '4096',
                    '--identifier', 'org.unixnu.x910.gles-probe', str(binary)], check=True)
    signature = subprocess.run(['codesign', '-d', '--verbose=4', str(binary)], capture_output=True, text=True, check=True)
    cdhash = re.search(r'^CDHash=([0-9a-f]{40})$', signature.stderr, re.M)
    if not cdhash:
        raise SystemExit('Signed CDHash was not returned')
    report = dict(status='built; not deployed or hardware tested', command=command,
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  executable_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), cdhash=cdhash[1],
                  target='arm64 iOS12+; deliberate match to the supplied arm64 game',
                  output_report='Data /private/var/db/x910-gles-probe.txt, X910REPORT round 1500')
    (args.output/'build-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
