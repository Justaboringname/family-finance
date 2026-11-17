#!/bin/bash
# 「家庭资产.app」的启动脚本（由 Contents/MacOS/launcher 调用，放在 Contents/Resources/ 里）。
# 1. 把应用里带的数据副本拷到 ~/Library/Application Support/家庭资产/：应用可能被系统放在只读位置运行，
#    而网站每次打开都要往库里写行情缓存和净资产快照。应用里的副本更新过（数据版本变了）才覆盖。
# 2. 网站已经在运行就直接打开浏览器；否则用应用自带的 Python 在 127.0.0.1 上启动，等它就绪再打开浏览器。
# 只用 macOS 自带的命令（bash 3.2），不依赖 Homebrew、PATH 或用户的 shell 配置。
set -u
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

RES="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="家庭资产"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
LOG="$SUPPORT/server.log"
STATE="$SUPPORT/server.state"            # 一行：端口 进程号 数据版本
MARKER="family-finance-viewer"           # 写在网站进程的命令行里，用来认出自己的进程
PY="$RES/python/bin/python3"
SNAP_VER="$(cat "$RES/snapshot/VERSION")"
SNAP_DATE="$(cat "$RES/snapshot/DATE")"
PREFERRED_PORT=18501                     # 不用 8501，免得和开发机上的网站撞端口

alert() {
    osascript -e 'on run argv' \
        -e 'display dialog (item 1 of argv) with title "家庭资产" buttons {"好"} default button 1 with icon caution' \
        -e 'end run' "$1" >/dev/null 2>&1
}

healthy() {
    [ "$(curl -s -m 2 "http://127.0.0.1:$1/_stcore/health" 2>/dev/null)" = "ok" ]
}

read_state() {
    PORT="" PID="" VER=""
    if [ -f "$STATE" ]; then
        read -r PORT PID VER < "$STATE"
    fi
}

stop_server() {
    read_state
    if [ -n "$PID" ] && ps -p "$PID" -o command= 2>/dev/null | grep -q "$MARKER"; then
        kill "$PID" 2>/dev/null
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.3
        done
    fi
    rm -f "$STATE"
}

APP="$(cd "$RES/../.." && pwd)"
case "$APP" in
    */AppTranslocation/*)   # 没挪位置就打开的下载应用，系统会把它放在临时的只读位置运行
        alert "请先把「家庭资产」拖进「应用程序」文件夹，再从「应用程序」里打开它。"
        exit 1 ;;
esac
# 能跑到这里说明用户已经同意打开这个应用（系统设置里点过「仍要打开」）。去掉整个应用的「从网上下载」标记，
# 应用里自带的 Python 和各个组件就不会再被系统逐个拦下来。只在还有标记时做（看应用本身和自带的 Python）。
mkdir -p "$SUPPORT"
if xattr -p com.apple.quarantine "$APP" >/dev/null 2>&1 || xattr -p com.apple.quarantine "$PY" >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$APP" 2>> "$SUPPORT/launch.log"
fi

# 已经在运行、而且是同一份数据：直接打开
read_state
if [ -n "$PORT" ] && [ "$VER" = "$SNAP_VER" ] && healthy "$PORT"; then
    open "http://127.0.0.1:$PORT/"
    exit 0
fi
stop_server

# 应用换了新数据（或第一次打开）：覆盖本机副本
if [ "$(cat "$SUPPORT/VERSION" 2>/dev/null)" != "$SNAP_VER" ] || [ ! -f "$SUPPORT/finance.db" ]; then
    if ! { cp -f "$RES/snapshot/finance.db" "$SUPPORT/finance.db.tmp" &&
           chmod 644 "$SUPPORT/finance.db.tmp" &&
           mv -f "$SUPPORT/finance.db.tmp" "$SUPPORT/finance.db" &&
           echo "$SNAP_VER" > "$SUPPORT/VERSION"; }; then
        alert "数据没能复制到这台电脑上（$SUPPORT）。请截图发给帮你装这个 app 的家人。"
        exit 1
    fi
fi

PORT="$("$PY" -I -c "
import socket
for p in ($PREFERRED_PORT, 0):
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', p))
        print(s.getsockname()[1])
        break
    except OSError:
        pass
    finally:
        s.close()
")"
if [ -z "$PORT" ]; then
    alert "自带的 Python 没能运行。请截图发给帮你装这个 app 的家人。"
    exit 1
fi

cd "$RES/app" || exit 1
FAMILY_FINANCE_DB="$SUPPORT/finance.db" \
FAMILY_FINANCE_READ_ONLY=1 \
FAMILY_FINANCE_SNAPSHOT="$SNAP_DATE" \
nohup "$PY" -B -E -s "$RES/server.py" "$MARKER" "$PORT" > "$LOG" 2>&1 < /dev/null &
PID=$!
echo "$PORT $PID $SNAP_VER" > "$STATE"

# 最多等 2 分钟：第一次打开时系统要逐个检查应用里的文件，会比平时慢
for _ in $(seq 1 240); do
    if healthy "$PORT"; then
        open "http://127.0.0.1:$PORT/"
        exit 0
    fi
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
done
alert "网站没能启动。请把这个文件发给帮你装这个 app 的家人：$LOG"
exit 1
