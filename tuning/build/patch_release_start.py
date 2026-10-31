"""Apply this fork's two startup patches to the start.sh shipped in the official
RetyOs v0.1.0-alpha.1 installer.

Why patch the release copy instead of dropping in the repo's start.sh: the
release was built from an older tree than this fork's base commit (6beab033),
and it differs in ways that are not only cosmetic -- 6beab033 clamps
`requested_ns` and `start_ns` before printing the cold-boot duration, adds
KINDLE_RECOVERY_DEFER_REFRESH, and asserts the overlay-cleanup anchor. Those
belong to a different session-controller build. Substituting the whole script
would pair new shell with old binaries; patching in place keeps the pairing the
author shipped.

The two patches themselves are self-contained shell and were carried over
unchanged from the fork's start.sh:

  A1  poll lab126_gui / x every 200 ms instead of every 1 s, same 30 s budget
  A2  cache bootstrap tarball digests by size:mtime instead of re-hashing
"""

import argparse
import hashlib
import sys
from pathlib import Path

POLL_BLOCK = """# --- startup tuning: sub-second service-stop polling ------------------------
# lab126_gui and x reach stop/waiting in well under a second, but a 1 s tick
# pays a full second before the first check and rounds every stop up to the
# next whole second -- twice, once per service. Poll at 200 ms over the same
# 30 s budget. Firmwares without usleep keep the original 1 s tick.
POLL_UNIT_MS=1000
if usleep 1000 2>/dev/null; then POLL_UNIT_MS=200; fi
POLL_LIMIT=$(( 30000 / POLL_UNIT_MS ))
poll_tick() {
    if [ "$POLL_UNIT_MS" = 200 ]; then usleep 200000 || sleep 1; else sleep 1; fi
}

# --- startup tuning: sha256 result cache ------------------------------------
# Every cold boot re-hashes the bootstrap tarballs (tens of MB on the FAT32
# user partition) only to compare them against a digest recorded at build time.
# Hashing is only required when the file may have changed, so remember the
# size:mtime signature next to the digest and skip the re-hash while both are
# unchanged. Any rewrite of the file updates mtime, which invalidates the entry;
# a cache miss simply falls back to hashing.
digest_cache() {  # $1=file $2=cache file -> prints sha256 of $1
    _dc_sig=$(stat -c '%s:%Y' "$1" 2>/dev/null || :)
    if [ -n "$_dc_sig" ] && [ -f "$2" ] && [ ! -L "$2" ]; then
        if [ "$(sed -n '1p' "$2" 2>/dev/null || :)" = "$_dc_sig" ]; then
            _dc_sum=$(sed -n '2p' "$2" 2>/dev/null || :)
            case "$_dc_sum" in
                ''|*[!0-9a-f]*) : ;;
                *) if [ "${#_dc_sum}" -eq 64 ]; then printf '%s\\n' "$_dc_sum"; return 0; fi ;;
            esac
        fi
    fi
    _dc_sum=$(sha256sum "$1" | awk '{print $1}')
    if [ -n "$_dc_sig" ]; then
        if printf '%s\\n%s\\n' "$_dc_sig" "$_dc_sum" > "$2.tmp.$$" 2>/dev/null; then
            mv "$2.tmp.$$" "$2" 2>/dev/null || rm -f "$2.tmp.$$" 2>/dev/null || :
        else
            rm -f "$2.tmp.$$" 2>/dev/null || :
        fi
    fi
    printf '%s\\n' "$_dc_sum"
}

"""

ANCHOR_BOOT_PROGRESS = "# BOOT_PROGRESS_UI=1. Stage-based progress"

OLD_GUI = """stopped=0
i=0
while [ "$i" -lt 30 ]; do
    status=$(/sbin/initctl status lab126_gui 2>&1 || true)
    case "$status" in *'stop/waiting'*) stopped=1; break;; esac
    i=$((i + 1)); sleep 1
done"""

NEW_GUI = """stopped=0
i=0
while [ "$i" -lt "$POLL_LIMIT" ]; do
    status=$(/sbin/initctl status lab126_gui 2>&1 || true)
    case "$status" in *'stop/waiting'*) stopped=1; break;; esac
    i=$((i + 1)); poll_tick
done"""

OLD_X = """x_stopped=0
i=0
while [ "$i" -lt 30 ]; do
    x_status=$(/sbin/initctl status x 2>&1 || true)
    case "$x_status" in *'stop/waiting'*) x_stopped=1; break;; esac
    i=$((i + 1)); sleep 1
done"""

NEW_X = """x_stopped=0
i=0
while [ "$i" -lt "$POLL_LIMIT" ]; do
    x_status=$(/sbin/initctl status x 2>&1 || true)
    case "$x_status" in *'stop/waiting'*) x_stopped=1; break;; esac
    i=$((i + 1)); poll_tick
done"""

OLD_SYS = """[ "$(sha256sum "$BASE/system-cache-bootstrap.tar" | awk '{print $1}')" = "$(sed -n '3p' "$cache_ready")" ]"""

NEW_SYS = """[ "$(digest_cache "$BASE/system-cache-bootstrap.tar" "$BASE/system-cache-bootstrap.sha-cache")" = "$(sed -n '3p' "$cache_ready")" ]"""

OLD_FIXED = """[ "$(sha256sum "$BASE/fixed-bootstrap.tar" | awk '{print $1}')" = "$(sed -n '3p' "$BASE/fixed-runtime.ready")" ]"""

NEW_FIXED = """[ "$(digest_cache "$BASE/fixed-bootstrap.tar" "$BASE/fixed-bootstrap.sha-cache")" = "$(sed -n '3p' "$BASE/fixed-runtime.ready")" ]"""

REPLACEMENTS = [
    ('A1 insert definitions', ANCHOR_BOOT_PROGRESS, POLL_BLOCK + ANCHOR_BOOT_PROGRESS, 1),
    ('A1 lab126_gui poll', OLD_GUI, NEW_GUI, 1),
    ('A1 x poll', OLD_X, NEW_X, 1),
    ('A2 system-cache digest', OLD_SYS, NEW_SYS, 1),
    ('A2 fixed-bootstrap digest', OLD_FIXED, NEW_FIXED, 1),
]


def apply(text):
    report = []
    for name, old, new, expected in REPLACEMENTS:
        count = text.count(old)
        if count != expected:
            raise SystemExit(f'{name}: anchor found {count}x, expected {expected}. '
                             f'Base start.sh changed; update the anchors instead of guessing.')
        text = text.replace(old, new, expected)
        report.append((name, count))
    return text, report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True, help='start.sh extracted from the official tar')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    src = Path(args.base)
    raw = src.read_bytes()
    if b'\r\n' in raw:
        raise SystemExit('base has CRLF line endings')
    text, report = apply(raw.decode('utf-8'))
    out = Path(args.out)
    out.write_bytes(text.encode('utf-8'))

    for name, count in report:
        print(f'  {name:26s} applied x{count}')
    print(f'{src.name}: {len(raw)} -> {out.stat().st_size} bytes')
    print('sha256', hashlib.sha256(out.read_bytes()).hexdigest()[:16])


if __name__ == '__main__':
    sys.exit(main())
