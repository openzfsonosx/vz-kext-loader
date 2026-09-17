#!/bin/bash
# Assemble a self-contained, relocatable Python+r2 runtime for the .app bundle.
#
#   ./build-runtime.sh                 # build ./runtime (staging dir)
#   ./build-runtime.sh /path/to/out    # build into a specific dir
#
# Produces a tree the app runs with zero external dependencies:
#
#   runtime/
#     python/           python-build-standalone (relocatable CPython)
#       bin/python3      + pyimg4, capstone (and their native wheels) installed
#     r2/
#       bin/radare2      radare2, dylibs relocated to @loader_path (no Homebrew)
#       lib/libr_*.dylib
#     vzkl/             the engine package (copied from ../engine/vzkl)
#
# build-app.sh copies this into VZKextLoader.app/Contents/Resources/runtime.
# Everything here is re-signed with a Developer ID by build-app.sh (inside-out)
# before notarization; this script only assembles + relocates.
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-runtime}"

# Pinned, known-good CPython (python-build-standalone). Override PY_VER/PY_TAG to
# bump. install_only = the relocatable, ready-to-run flavor.
PY_TAG="${PY_TAG:-20260901}"
PY_VER="${PY_VER:-3.12.14}"
PY_ARCH="${PY_ARCH:-aarch64}"
PY_ASSET="cpython-${PY_VER}+${PY_TAG}-${PY_ARCH}-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_ASSET}"

# radare2 to vendor. Defaults to the Homebrew install; override R2_PREFIX.
R2_PREFIX="${R2_PREFIX:-$(brew --prefix radare2 2>/dev/null || echo /opt/homebrew/opt/radare2)}"

say() { printf '>> %s\n' "$*"; }

rm -rf "$OUT"
mkdir -p "$OUT"

# ---------------------------------------------------------------------------
# B1: relocatable CPython
# ---------------------------------------------------------------------------
say "fetching CPython ${PY_VER} (${PY_TAG})"
CACHE="${TMPDIR:-/tmp}/vzkl-$PY_ASSET"
[ -f "$CACHE" ] || curl -fsSL "$PY_URL" -o "$CACHE"
tar xzf "$CACHE" -C "$OUT"          # -> $OUT/python
PY="$OUT/python/bin/python3"
"$PY" --version

# ---------------------------------------------------------------------------
# B2: engine Python deps (pyimg4 pulls apple-compress/pycryptodome/pylzss;
#     capstone pulls libcapstone.dylib) installed as real files in site-packages
# ---------------------------------------------------------------------------
say "installing pyimg4 + capstone into the bundled Python"
"$PY" -m pip install --quiet --disable-pip-version-check --no-warn-script-location \
      pyimg4 capstone
"$PY" - <<'PY'
import pyimg4, capstone, apple_compress
print(f"   pyimg4 {pyimg4.__version__}, capstone {capstone.__version__}, apple-compress OK")
PY

# ---------------------------------------------------------------------------
# B3: vendor radare2, relocate dylibs to @loader_path (drop share/ — not needed
#     for symbol listing, which is all the engine uses)
# ---------------------------------------------------------------------------
say "vendoring radare2 from $R2_PREFIX"
[ -x "$R2_PREFIX/bin/radare2" ] || { echo "radare2 not found at $R2_PREFIX/bin" >&2; exit 1; }
mkdir -p "$OUT/r2/bin" "$OUT/r2/lib"
cp "$R2_PREFIX/bin/radare2" "$OUT/r2/bin/radare2"
ln -sf radare2 "$OUT/r2/bin/r2"
# real (versioned) dylibs, then recreate the unversioned symlinks the loader uses
for real in "$R2_PREFIX/lib/"libr_*.*.*.dylib; do cp "$real" "$OUT/r2/lib/"; done
for f in "$OUT/r2/lib/"libr_*.*.*.dylib; do
    b=$(basename "$f"); ln -sf "$b" "$OUT/r2/lib/${b%%.*}.dylib"
done
chmod -R u+w "$OUT/r2"

say "relocating radare2 dylib references to @loader_path"
_reloc() {
    local file="$1" prefix="$2"
    otool -L "$file" 2>/dev/null | awk '/homebrew.*libr_/{print $1}' | while read -r ref; do
        install_name_tool -change "$ref" "$prefix/$(basename "$ref")" "$file" 2>/dev/null
    done
}
_reloc "$OUT/r2/bin/radare2" "@loader_path/../lib"
for d in "$OUT/r2/lib/"libr_*.*.*.dylib; do
    _reloc "$d" "@loader_path"
    install_name_tool -id "@loader_path/$(basename "$d")" "$d" 2>/dev/null || true
done
# Verify nothing still points at Homebrew.
leftover=$(for f in "$OUT/r2/bin/radare2" "$OUT/r2/lib/"libr_*.*.*.dylib; do
    otool -L "$f" 2>/dev/null; done | grep -c homebrew || true)
[ "$leftover" -eq 0 ] || { echo "ERROR: $leftover Homebrew refs remain after relocation" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Slim: drop what a runtime doesn't need (keeps the .app lean).
# ---------------------------------------------------------------------------
say "slimming"
# Python: test suites, bytecode caches, idle/tkinter, pip's own cache.
find "$OUT/python" -type d \( -name '__pycache__' -o -name 'test' -o -name 'tests' \
     -o -name 'idlelib' -o -name 'turtledemo' \) -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$OUT/python/lib/python3.12/config-"* 2>/dev/null || true

# ---------------------------------------------------------------------------
# B4 (data half): the engine package itself
# ---------------------------------------------------------------------------
say "copying vzkl engine"
rm -rf "$OUT/vzkl"
cp -R ../engine/vzkl "$OUT/vzkl"
find "$OUT/vzkl" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Self-test: run the full engine (pyimg4 + capstone + r2) self-contained.
# ---------------------------------------------------------------------------
say "self-test: importing engine + running r2 from the bundle"
PATH="$PWD/$OUT/r2/bin:$PATH" PYTHONPATH="$PWD/$OUT" R2_NOPLUGINS=1 "$PY" - <<'PY'
import shutil, subprocess
assert "runtime/r2/bin" in (shutil.which("r2") or ""), "bundled r2 not on PATH"
import vzkl.patch.kernelcache, vzkl.patch.iboot, vzkl.patch.img4   # noqa
ver = subprocess.run(["r2", "-v"], capture_output=True, text=True).stdout.splitlines()[0]
print(f"   engine imports OK; bundled {ver}")
PY

TOTAL=$(du -sh "$OUT" | awk '{print $1}')
say "done: $OUT ($TOTAL)"
