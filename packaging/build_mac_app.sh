#!/bin/bash
# 把网站打包成给家人用的 Mac 应用「家庭资产.app」：自带 Python 和依赖，带一份当前数据的副本（只读、不联网）。
# 对方不用装任何东西，双击应用就在对方自己电脑的浏览器里打开；数据不会同步，想更新就重跑一次、把新的 zip 发过去替换旧应用。
#
# 用法：packaging/build_mac_app.sh [输出目录，默认 ~/Downloads]
# 结果：<输出目录>/家庭资产-<日期>.zip。适用于 Apple 芯片（M1 及以后）、macOS 12 及以上的 Mac。
# 只读开发机上的 data/finance.db（用 sqlite 的 .backup 拷一份），不会改它。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="${1:-$HOME/Downloads}"
APP_NAME="家庭资产"

# 可移植的 Python（python-build-standalone，astral-sh 维护），放到哪个目录都能跑
PBS_TAG=20261013
PBS_FILE="cpython-3.12.15+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_SHA256=9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE/+/%2B}"
MIN_MACOS=12.0              # pyarrow 26 的 arm64 包最低要求 macOS 12
WHEEL_PLATFORM=macosx_12_0_arm64

CACHE="$HOME/Library/Caches/family-finance-packaging"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/$APP_NAME.app"
RES="$APP/Contents/Resources"
PY="$RES/python/bin/python3"
SITE="$RES/python/lib/python3.12/site-packages"
DATE="$(date +%F)"
VER="$(date +%Y%m%d%H%M%S)"

step() { printf '\n== %s\n' "$1"; }

step "准备 Python"
mkdir -p "$CACHE" "$APP/Contents/MacOS" "$RES/snapshot" "$OUT"
if ! echo "$PBS_SHA256  $CACHE/$PBS_FILE" | shasum -a 256 -c --status 2>/dev/null; then
    curl -fL --retry 3 -o "$CACHE/$PBS_FILE" "$PBS_URL"
    echo "$PBS_SHA256  $CACHE/$PBS_FILE" | shasum -a 256 -c
fi
tar -xzf "$CACHE/$PBS_FILE" -C "$RES"          # 解出 Resources/python/

step "安装依赖（只要 macOS $MIN_MACOS 能用的二进制包）"
"$PY" -m pip install --quiet --disable-pip-version-check --no-compile \
    --only-binary=:all: --platform "$WHEEL_PLATFORM" --python-version 3.12 --implementation cp --abi cp312 \
    --target "$SITE" -r "$HERE/requirements-app.txt" -c "$HERE/constraints.txt"

step "精简"
rm -rf "$SITE/bin" "$SITE/share" "$RES/python/bin/pip"* "$RES/python/bin/idle"* "$RES/python/bin/pydoc"* \
       "$RES/python/include" "$RES/python/share" \
       "$RES/python/lib/python3.12/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,site-packages/pip} \
       "$RES/python/lib/"{tcl,tk,itcl,thread}* "$RES/python/lib/"lib{tcl,tk}* \
       "$RES/python/lib/python3.12/lib-dynload/"_tkinter* \
       "$SITE/pyarrow/include" "$SITE/pyarrow/src" "$SITE/pyarrow/tests" "$SITE/pandas/tests"
find "$SITE" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$RES/python" \( -name '*.pyx' -o -name '*.pxd' -o -name '*.pyi' \) -delete

step "复制网站代码"
mkdir -p "$RES/app"
cp -R "$ROOT/app.py" "$ROOT/ui_common.py" "$ROOT/ui_html.py" "$ROOT/ui_theme.py" \
      "$ROOT/core" "$ROOT/views" "$ROOT/assets" "$ROOT/tax_params" "$ROOT/.streamlit" "$RES/app/"
find "$RES/app" -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/launch.sh" "$HERE/server.py" "$RES/"
chmod 755 "$RES/launch.sh"

step "数据副本"
SNAP="$RES/snapshot/finance.db"
/usr/bin/sqlite3 "$ROOT/data/finance.db" ".backup '$SNAP'"
# 副本固定离线（国内连不上 Yahoo，开着会卡住首次打开）；可修改账号名单在副本里没用，删掉
/usr/bin/sqlite3 "$SNAP" "INSERT INTO settings(key, value) VALUES('offline', '1')
                          ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                          DELETE FROM settings WHERE key = 'editor_logins';
                          VACUUM;"
echo "$VER" > "$RES/snapshot/VERSION"
echo "$DATE" > "$RES/snapshot/DATE"

step "预编译 .pyc（运行时不往应用里写文件，否则签名会失效）"
"$PY" -m compileall -q -f -j0 --invalidation-mode unchecked-hash "$RES/python/lib/python3.12" "$RES/app" \
    || echo "（个别文件编译失败，不影响运行，只是那几个文件第一次导入稍慢）"

step "启动器和图标"
xcrun clang -O2 -arch arm64 -mmacosx-version-min="$MIN_MACOS" -o "$APP/Contents/MacOS/launcher" "$HERE/launcher.c"
"$PY" -I - "$ROOT/assets/icon.svg" "$WORK/icon1024.svg" <<'PY'
import re, sys
inner = re.sub(r"^\s*<svg[^>]*>|</svg>\s*$", "", open(sys.argv[1], encoding="utf-8").read().strip())
# macOS 图标网格：1024 画布里 824 的圆角方块，四周留白
open(sys.argv[2], "w", encoding="utf-8").write(
    '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
    f'<g transform="translate(100 100) scale(8.24)">{inner}</g></svg>')
PY
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
swift "$HERE/render_icon.swift" "$WORK/icon1024.svg" "$WORK/icon1024.png" 1024
for s in 16 32 128 256 512; do
    sips -z $s $s "$WORK/icon1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$WORK/icon1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.justaboringname.family-finance.viewer</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$DATE</string>
  <key>CFBundleVersion</key><string>$VER</string>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

step "签名（没有开发者证书，用临时签名；Apple 芯片要求每个可执行文件都有签名）"
xattr -cr "$APP"
find "$RES" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 |
    while IFS= read -r -d '' f; do
        if file -b "$f" | grep -q 'Mach-O'; then
            codesign --force --sign - "$f" 2>"$WORK/codesign.err" || { cat "$WORK/codesign.err"; exit 1; }
        fi
    done
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"

step "压缩"
ZIP="$OUT/$APP_NAME-$DATE.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo "应用 $(du -sh "$APP" | cut -f1)，压缩包 $(du -h "$ZIP" | cut -f1)：$ZIP"
