#!/bin/bash
# 工作日收盘后在 Safari 里打开「家族资产总览」，看当天赚了多少；再在后台记一条当天的净资产快照。
# 由 LaunchAgent com.justaboringname.family-finance.after-close 在每个工作日 16:10（美东）调用。
# - Safari 里已有这个网站的标签页就把它刷新到总览页（刷新会重新联网取收盘价），没有就新开一个。
# - 正在玩 LoL / MoSim 时只在后台刷新/开标签页，不把 Safari 拉到前面。
# - 快照由 scripts/record_snapshot.py 直接记，不靠网页：Mac 重启后 Safari 会停在「此连接不安全」页，网站根本没打开。
# - 最后由 scripts/push_phone_after_close.sh 把快照推到 iPhone 上的「家庭资产」app；手机不在线就隔 25 分钟重试，最晚到美东当天结束。
# 用法：open_after_close.sh [--now]    --now 跳过时间检查（测试用）
set -u
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 用 localhost 不用 127.0.0.1：这台 Safari 开着「仅限 HTTPS」，http://127.0.0.1 会被直接拦掉，localhost 可以打开
URL="http://localhost:8501/"
log() { echo "$(date '+%F %T') $*"; }

if [ "${1:-}" != "--now" ]; then
    # 按美东时间判断，只在工作日 16:00–23:59 打开。Mac 睡着错过了时间，launchd 会在唤醒时补跑一次；
    # 过了当天就不补，免得第二天早上弹出昨天的页面。
    dow=$(TZ=America/New_York date +%u)
    hour=$(TZ=America/New_York date +%H)
    if [ "$dow" -gt 5 ] || [ "$hour" -lt 16 ]; then
        log "跳过：美东周$dow ${hour}点"
        exit 0
    fi
fi

# 网站由另一个 LaunchAgent 常驻；刚开机时可能还没起来，最多等 30 秒
for _ in $(seq 1 30); do
    [ "$(curl -s -m 2 http://127.0.0.1:8501/_stcore/health)" = "ok" ] && break
    sleep 1
done

FRONT=1
if pgrep -iq 'League|Riot|MoSim'; then
    FRONT=0
fi

# 找已有的标签页并刷新；第一次运行时 macOS 可能会问是否允许控制 Safari。
# 一分钟内没完成（没人点允许，或被拒绝）就退回到直接新开标签页。
# 刷新后等几秒读标签页标题：停在「此连接不安全」页说明网站没打开，写进日志。
TITLE_FILE=$(mktemp)
osascript - "$URL" "$FRONT" > "$TITLE_FILE" <<'APPLESCRIPT' &
on run argv
    set u to item 1 of argv
    set bringFront to (item 2 of argv) is "1"
    tell application "Safari"
        set found to false
        repeat with w in windows
            repeat with t in tabs of w
                set tu to URL of t
                if tu is not missing value and (tu starts with "http://127.0.0.1:8501" or tu starts with "http://localhost:8501") then
                    set target to contents of t
                    set URL of target to u
                    set current tab of w to target
                    if bringFront then set index of w to 1
                    set found to true
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat
        if not found then
            if (count of windows) is 0 then
                make new document
                set target to current tab of window 1
            else
                tell window 1
                    set target to make new tab
                    set current tab to target
                end tell
            end if
            set URL of target to u
        end if
        if bringFront then activate
        delay 6
        try
            return name of target
        on error
            return ""
        end try
    end tell
end run
APPLESCRIPT
pid=$!
( sleep 60; kill "$pid" 2>/dev/null ) &
watchdog=$!
if wait "$pid" 2>/dev/null; then   # 超时被杀时 bash 会把整段 AppleScript 当作任务名写进日志
    kill "$watchdog" 2>/dev/null; wait "$watchdog" 2>/dev/null   # 不 wait 的话，bash 会往日志里写一行「Terminated」
    title=$(tr -d '\n' < "$TITLE_FILE")
    case "$title" in
        *不安全*|*"Not Secure"*|*"not secure"*)
            log "Safari 停在「${title}」页，网站没打开（Mac 重启后常见）：在那个标签页点「仍要访问」" ;;
        *)
            [ "$FRONT" = 1 ] && log "已在 Safari 打开（标签页：${title}）" \
                || log "游戏在运行，已在 Safari 后台打开（标签页：${title}）" ;;
    esac
else
    kill "$watchdog" 2>/dev/null; wait "$watchdog" 2>/dev/null
    if [ "$FRONT" = 1 ]; then open -a Safari "$URL"; else open -g -a Safari "$URL"; fi
    log "没能控制 Safari（没有权限或超时），已直接新开标签页"
fi
rm -f "$TITLE_FILE"

# 记当天的净资产快照。Yahoo 偶尔收盘后几分钟才出当天收盘价，没记到今天就隔 5 分钟再试，最多 3 次
"$ROOT/.venv/bin/python" "$ROOT/scripts/record_snapshot.py" 2>&1 | while IFS= read -r line; do log "$line"; done

# 把最新快照推到 iPhone（要排在记快照之后：导出用的是刚取到的缓存行情）。它自己写日志、自己重试，退出码恒为 0
/bin/bash "$ROOT/scripts/push_phone_after_close.sh"
