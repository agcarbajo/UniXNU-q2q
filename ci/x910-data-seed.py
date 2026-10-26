#!/usr/bin/env python3
"""Build the X910 Data seed on the macOS runner with sheng's own tools, and publish it only encrypted.

Steps, all with the UniXNU scripts of a pinned commit:
1. Read the pinned iPad7,1 21H461 OS member from Apple's IPSW URL (range requests, sha256-pinned) and attach it
   read-only; prepare-owner-data-fixture.py checks the original launchd hash, formats a 32 MiB APFS "OwnerData" volume
   and copies the OS's initial private/var tree (without staged_system_apps).
2. Pack it as DBRDISK1 (drivers/DarwinBoot/Test/packed_ramdisk.py), mark Setup done (seed-data-skip-setup.py: there
   is no touch input yet) and grow the container to the requested size (seed-data-grow.py; sparse, volume UUID kept).
3. Encrypt the final seed with a random AES-256 key wrapped by the owner's RSA public key (ci/x910-owner-public.pem),
   so the public run's artifact never exposes the Apple files inside. Only the volume UUID and hashes are plain.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
UNIXNU = 'https://github.com/UniXNU/UniXNU.git'
UNIXNU_COMMIT = '82dd277f51d5e5bb6430a51c13c3f8e5fafc5505'
SHENG = 'https://github.com/UniXNU/UniXNU-sheng.git'
SHENG_COMMIT = 'ae61b4d7849ef816c7ed1b67a5d7d3c0742b6282'


def run(*command, **kwargs):
    # The artifact is public: never expose the symmetric seed key in CI logs.
    display = list(map(str, command))
    for index, value in enumerate(display[:-1]):
        if value == '-K':
            display[index + 1] = '[redacted]'
    print('+', ' '.join(display), flush=True)
    return subprocess.run([str(c) for c in command], check=True, **kwargs)


def fetch(target, url, commit):
    run('git', 'init', '-q', target)
    run('git', '-C', target, 'fetch', '-q', '--depth', '1', url, commit)
    run('git', '-C', target, 'checkout', '-q', 'FETCH_HEAD')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--gib', type=int, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    out = work/'artifacts/x910-data-seed'
    out.mkdir(parents=True, exist_ok=True)
    tree = work/'seed-unixnu'
    fetch(tree, UNIXNU, UNIXNU_COMMIT)
    run('git', '-C', tree, 'submodule', 'update', '--init', '--depth', '1', 'drivers/DarwinBoot')
    sheng = work/'seed-sheng'
    fetch(sheng, SHENG, SHENG_COMMIT)

    # 1. Original OS member, pinned, attached read-only where sheng's fixture tool expects it.
    sys.path.insert(0, str(tree/'scripts'))
    from unixnu.ipsw import Ipsw
    import tomllib

    class Context:
        out_root = work/'ipsw-cache'
        def option_text(self, *a): return None
    ctx = Context()
    ctx.board = tomllib.loads((sheng/'board.toml').read_text())
    dmg = Ipsw(ctx).extract('OS')
    mount = tree/'.cache/firmware/target-volumes/os-mount'
    mount.mkdir(parents=True, exist_ok=True)
    attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly', '-nobrowse', '-noverify',
                                                       '-mountpoint', str(mount), '-plist', str(dmg)]))
    print(json.dumps([e.get('dev-entry') for e in attached['system-entities']]))
    try:
        run(sys.executable, tree/'scripts/prepare-owner-data-fixture.py', '--output', tree/'build/owner-data-fixture', cwd=tree)
    finally:
        subprocess.run(['hdiutil', 'detach', '-force', str(mount)], capture_output=True)
    fixture = json.loads((tree/'build/owner-data-fixture/seed.json').read_text())

    # 2. DBRDISK1, Setup done, grown container.
    sys.path.insert(0, str(tree/'drivers/DarwinBoot/Test'))
    import packed_ramdisk
    base = tree/'build/owner-data-base.dbr'
    packed_ramdisk.pack(tree/'build/owner-data-fixture/owner-data-32.cdr', base)
    skip = tree/'build/owner-data-skip-setup.dbr'
    run(sys.executable, tree/'scripts/seed-data-skip-setup.py', base, skip, cwd=tree)
    final = tree/'build/x910-data.dbr'
    run(sys.executable, tree/'scripts/seed-data-grow.py', '--gib', str(args.gib), skip, final, cwd=tree)
    logical, extents = packed_ramdisk.regions(final.read_bytes())

    # 3. Encrypt for the owner only.
    key, iv = os.urandom(32), os.urandom(16)
    (work/'seed.key').write_bytes(key)
    run('openssl', 'enc', '-aes-256-ctr', '-K', key.hex(), '-iv', iv.hex(), '-in', final, '-out', out/'x910-data.dbr.enc')
    run('openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', ROOT/'ci/x910-owner-public.pem',
        '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256', '-in', work/'seed.key', '-out', out/'x910-data.key.enc')
    (work/'seed.key').unlink()
    report = dict(volume_uuid=str(fixture['volume_uuid']).upper(), base_seed_sha256=fixture['sha256'],
                  final_sha256=hashlib.sha256(final.read_bytes()).hexdigest(), final_bytes=final.stat().st_size,
                  logical_bytes=logical, gib=args.gib, iv=iv.hex(), cipher='AES-256-CTR, key RSA-OAEP-SHA256 wrapped',
                  unixnu_commit=UNIXNU_COMMIT, os_member_pinned=True, skip_setup=True, plaintext_published=False)
    (out/'x910-data-seed.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    shutil.rmtree(tree/'build', ignore_errors=True)
    shutil.rmtree(work/'ipsw-cache', ignore_errors=True)


if __name__ == '__main__':
    main()
