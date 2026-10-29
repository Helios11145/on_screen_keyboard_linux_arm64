#!/usr/bin/env bash
# Builds Simple-OSK-<arch>.AppImage. Run on the machine you want it for (x86_64 or aarch64),
# or set ARCH=aarch64 / ARCH=x86_64 to build for the other one (it's just a Python script inside).
set -euo pipefail
cd "$(dirname "$0")"
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in arm64) ARCH=aarch64;; esac
HOST="$(uname -m)"; case "$HOST" in arm64) HOST=aarch64;; esac
TOOL="appimagetool-${HOST}.AppImage"
[ -f "$TOOL" ] || curl -L -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/$TOOL"
chmod +x "$TOOL"

rm -rf AppDir && mkdir -p AppDir/usr/share/osk
cp osk.py AppDir/usr/share/osk/osk.py

cat > AppDir/AppRun <<'EOS'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
LS="$( (/usr/bin/ldconfig -p 2>/dev/null || ldconfig -p 2>/dev/null) | awk '/libgtk-layer-shell\.so /{print $NF; exit}')"
[ -n "$LS" ] && export LD_PRELOAD="$LS${LD_PRELOAD:+:$LD_PRELOAD}"
exec python3 "$HERE/usr/share/osk/osk.py" "$@"
EOS
chmod +x AppDir/AppRun

cat > AppDir/simple-osk.desktop <<'EOS'
[Desktop Entry]
Type=Application
Name=Simple OSK
Comment=Simple on-screen keyboard
Exec=AppRun
Icon=simple-osk
Categories=Utility;Accessibility;
EOS

python3 - <<'EOP'
import zlib, struct
n = 128
raw = b"".join(b"\x00" + b"".join(
    (b"\x35\x84\xe4" if (x//16 + y//16) % 2 == 0 else b"\xff\xff\xff") for x in range(n)) for y in range(n))
def ch(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open("AppDir/simple-osk.png", "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
EOP

ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "./$TOOL" AppDir "Simple-OSK-${ARCH}.AppImage"
echo "Done: Simple-OSK-${ARCH}.AppImage"
