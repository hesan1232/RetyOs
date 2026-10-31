"""Verify a repacked RetyOs installer against the official base.

Checks, in order of how badly each one would hurt if it failed:

  1. every entry except the intentional replacements is byte-identical
  2. the entry set is the base set plus exactly the staged files
  3. payload.tar.gz still matches payload.sha256 (the 160 MB core must be untouched)
  4. every *.sha256 sidecar still verifies against its file
  5. the shipped start.sh is byte-identical to the patched copy we built from
  6. build-manifest.json records the new hashes and nothing else moved
  7. every shell script in the package passes `sh -n`
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_members(path):
    with tarfile.open(path) as t:
        out = {}
        for m in t.getmembers():
            out[m.name] = (m, t.extractfile(m).read() if m.isreg() else None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True)
    ap.add_argument('--new', required=True)
    ap.add_argument('--expect-start-sh', required=True)
    args = ap.parse_args()

    base = load_members(args.base)
    new = load_members(args.new)
    failures = []

    replaced = sorted(set(new) - set(base))
    changed = sorted(n for n in set(base) & set(new)
                     if base[n][1] is not None and new[n][1] is not None
                     and sha256(base[n][1]) != sha256(new[n][1]))
    removed = sorted(set(base) - set(new))

    print(f'entries   base={len(base)}  new={len(new)}  added={len(replaced)}  changed={len(changed)}  removed={len(removed)}')
    print(f'added:   {replaced}')
    print(f'changed: {changed}')
    if removed:
        failures.append(f'entries disappeared: {removed}')

    expected_changed = {'kindle-android/start.sh', 'build-manifest.json'}
    unexpected = set(changed) - expected_changed
    if unexpected:
        failures.append(f'unexpected content changes: {sorted(unexpected)}')
    for name in sorted(expected_changed - set(changed)):
        if name in changed or name not in new:
            continue
    if 'kindle-android/start.sh' not in changed:
        failures.append('start.sh did not change')
    else:
        want = Path(args.expect_start_sh).read_bytes()
        got = new['kindle-android/start.sh'][1]
        if got != want:
            failures.append('shipped start.sh differs from the patched copy')
        else:
            print(f'start.sh  matches patched copy  sha256={sha256(got)[:16]}  {len(got)} B')

    # payload and sidecars
    payload = new['kindle-android/payload.tar.gz'][1]
    payload_sum = sha256(payload)
    sidecar = new['kindle-android/payload.sha256'][1].decode().split()[0]
    print(f'payload   sha256={payload_sum[:16]}  sidecar={sidecar[:16]}  {"OK" if payload_sum == sidecar else "MISMATCH"}')
    if payload_sum != sidecar:
        failures.append('payload.tar.gz no longer matches payload.sha256')

    checked = 0
    # install-android.sh names this one payload.sha256 for payload.tar.gz
    special = {'kindle-android/payload.sha256': 'kindle-android/payload.tar.gz'}
    for name, (member, data) in new.items():
        if not name.endswith('.sha256') or data is None:
            continue
        target = special.get(name, name[:-len('.sha256')])
        if target not in new or new[target][1] is None:
            failures.append(f'sidecar without target: {name}')
            continue
        want = data.decode().split()[0]
        got = sha256(new[target][1])
        if want != got:
            failures.append(f'sidecar mismatch: {name}')
        checked += 1
    print(f'sidecars  {checked} verified')

    # manifest
    manifest = json.loads(new['build-manifest.json'][1].decode('utf-8-sig'))
    files = manifest.get('files', {})
    for name in ('kindle-android/start.sh',
                 'kindle-android/tuning/measure.sh',
                 'kindle-android/tuning/diagnose.sh',
                 'kindle-android/tuning/display-profile.sh',
                 'kindle-android/tuning/README.md'):
        if name not in files:
            continue
        if files[name] != sha256(new[name][1]):
            failures.append(f'manifest hash stale for {name}')
    stale = [n for n, h in files.items()
             if n in new and new[n][1] is not None and sha256(new[n][1]) != h]
    print(f'manifest  {len(files)} entries, stale={len(stale)}  modifiedFiles={manifest.get("modifiedFiles")}')
    if stale:
        failures.append(f'manifest stale for {len(stale)} files: {stale[:5]}')

    # shell syntax
    shells = [n for n in new if n.endswith('.sh') and new[n][1] is not None]
    bad = []
    for name in sorted(shells):
        r = subprocess.run(['sh', '-n'], input=new[name][1], capture_output=True)
        if r.returncode:
            bad.append((name, r.stderr.decode(errors='replace').strip()[:120]))
    print(f'shell     {len(shells)} scripts checked, {len(bad)} failed')
    for name, err in bad:
        print(f'  {name}: {err}')
        failures.append(f'syntax error in {name}')

    print()
    if failures:
        print('FAILED')
        for f in failures:
            print('  -', f)
        return 1
    print('ALL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
