"""Repack a RetyOs install tar with this fork's start.sh and tuning tools.

Why this does not compile anything: start.sh and everything under
device/kindle-android/ is plain shell that tools/build_system.py copies into the
installer verbatim (build_system.py:229-231). The compiled pieces -- payload.tar.gz,
gralloc.hwtcon.so, session-controller and friends -- are untouched, so a full
`python build.py all` (Rust + NDK + JDK8 + the API25 image, ~41 steps) would
produce byte-identical versions of them.

The replacement is done by walking the source tar and substituting entries as
they stream through, so the 180 MB payload is copied rather than held in memory.
"""

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

MANIFEST = 'build-manifest.json'


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_text_lf(path):
    data = path.read_bytes()
    if data.startswith(b'\xef\xbb\xbf'):
        raise SystemExit(f'{path}: UTF-8 BOM would break the shell shebang')
    if b'\r\n' in data:
        raise SystemExit(f'{path}: CRLF line endings would break the shell shebang')
    return data


def collect(repo, start_sh):
    """Files this fork owns: start.sh plus the read-only tuning tools."""
    stage = {}
    stage['kindle-android/start.sh'] = (read_text_lf(start_sh), 0o755)

    tuning = repo / 'tuning'
    for path in sorted(tuning.iterdir()):
        if path.is_file() and path.suffix in ('.sh', '.md'):
            name = f'kindle-android/tuning/{path.name}'
            stage[name] = (read_text_lf(path), 0o755 if path.suffix == '.sh' else 0o644)
    return stage


def update_manifest(data, stage, replaced):
    """Keep build-manifest.json honest about the files we swapped.

    install-android.sh does not consult this file, but leaving stale hashes in a
    shipped manifest is the kind of thing that costs an hour six weeks later.
    """
    try:
        doc = json.loads(data.decode('utf-8-sig'))
    except Exception:
        return data, False
    files = doc.get('files')
    if not isinstance(files, dict):
        return data, False
    changes = []
    for name, (content, _mode) in stage.items():
        key = name  # manifest keys are relative to out/
        if files.get(key) != sha256_bytes(content):
            files[key] = sha256_bytes(content)
            changes.append(key)
    if not changes:
        return data, False
    doc['modifiedBy'] = 'hesan1232/RetyOs tuning fork'
    doc['modifiedFiles'] = sorted(set(doc.get('modifiedFiles', [])) | set(changes))
    return (json.dumps(doc, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), True


def repack(base, repo, out, start_sh):
    stage = collect(repo, start_sh)
    seen = set()
    manifest_report = {}
    total_in = total_out = 0

    with tarfile.open(base, 'r') as tin, tarfile.open(out, 'w', format=tarfile.PAX_FORMAT) as tout:
        for member in tin:
            name = member.name.lstrip('./')
            total_in += 1
            if name in stage:
                content, mode = stage[name]
                member = _clone(member, len(content), mode)
                tout.addfile(member, io.BytesIO(content))
                seen.add(name)
                continue
            if name == MANIFEST and member.isreg():
                content = tin.extractfile(member).read()
                content, changed = update_manifest(content, stage, seen)
                if changed:
                    member = _clone(member, len(content), member.mode)
                    tout.addfile(member, io.BytesIO(content))
                else:
                    tout.addfile(member, io.BytesIO(content))
                continue
            if member.isreg():
                tout.addfile(member, tin.extractfile(member))
            else:
                tout.addfile(member)
            total_out += 1

        for name, (content, mode) in stage.items():
            if name in seen:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            tout.addfile(info, io.BytesIO(content))
            seen.add(name)

    missing = set(stage) - seen
    if missing:
        raise SystemExit('staged files never written: ' + ', '.join(sorted(missing)))
    return stage


def _clone(member, size, mode):
    clone = tarfile.TarInfo(member.name)
    clone.size = size
    clone.mode = mode
    clone.mtime = 0
    clone.uid = clone.gid = 0
    clone.uname = clone.gname = ''
    clone.type = tarfile.REGTYPE
    return clone


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True, help='official kindle-android-install.tar')
    ap.add_argument('--repo', default=r'D:/AI/rea/RetyOs')
    ap.add_argument('--out', required=True)
    ap.add_argument('--start-sh', help='patched start.sh; defaults to the repo copy')
    args = ap.parse_args()

    base = Path(args.base)
    repo = Path(args.repo)
    out = Path(args.out)
    if not base.is_file():
        raise SystemExit(f'base not found: {base}')
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f'base  {base}  {base.stat().st_size/1024/1024:.1f} MB  sha256={sha256_file(base)[:16]}')
    start_sh = Path(args.start_sh) if args.start_sh else repo / 'device/kindle-android/start.sh'
    if not start_sh.is_file():
        raise SystemExit(f'start.sh not found: {start_sh}')
    stage = repack(base, repo, out, start_sh)
    print(f'out   {out}  {out.stat().st_size/1024/1024:.1f} MB  sha256={sha256_file(out)[:16]}')
    for name, (content, mode) in stage.items():
        print(f'  replaced  {name:44s} {len(content):7d} B  mode={oct(mode)}  sha256={sha256_bytes(content)[:16]}')


if __name__ == '__main__':
    sys.exit(main())
