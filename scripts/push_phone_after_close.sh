#!/bin/bash
# 收盘后把最新快照推到 iPhone 上的「家庭资产」app。由 open_after_close.sh 在记完当天净资产快照之后调用。
# 一次推不上（手机不在同一 Wi-Fi、关机、锁屏太久）就隔 25 分钟再试，到开始那天的美东 23:59:59 为止，下个交易日收盘后再推。
# 截止时间在开始时算死：Mac 睡着时 sleep 不走，睡过夜醒来也不能接着试——任务还在跑的话，launchd 第二天 16:10 不会再起它，
# 那天的 Safari 刷新和记快照就都没了。
# 每次尝试写一行日志。不管成没成功，退出码都是 0，不影响收盘任务。
# 用法：scripts/push_phone_after_close.sh
#   测试用的环境变量：FF_PHONE_UDID 指定设备，FF_PHONE_RETRY_WAIT 重试间隔（秒，默认 1500），FF_PHONE_TRIES 最多试几次（默认不限），
#   FF_PHONE_DEADLINE 截止时间（epoch 秒，默认今天美东 23:59:59）
set -u
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUSH="$ROOT/ios-app/scripts/push-snapshot.sh"
WAIT="${FF_PHONE_RETRY_WAIT:-1500}"
TRIES="${FF_PHONE_TRIES:-0}"
DEADLINE="${FF_PHONE_DEADLINE:-$(TZ=America/New_York date -j -f '%Y-%m-%d %H:%M:%S' "$(TZ=America/New_York date +%Y-%m-%d) 23:59:59" +%s)}"
log() { echo "$(date '+%F %T') [手机] $*"; }

[ -f "$ROOT/ios-app/Config/Local.xcconfig" ] || { log "跳过：没有 ios-app/Config/Local.xcconfig（这台 Mac 没装过手机 app）"; exit 0; }

n=0
while :; do
    n=$((n + 1))
    out="$(/bin/bash "$PUSH" 2>&1)"; rc=$?
    msg="$(printf '%s' "$out" | tr '\n' ' ' | sed 's/  */ /g; s/ $//')"
    if [ $rc -eq 0 ]; then
        log "第 $n 次：$msg"
        break
    elif [ $rc -eq 1 ]; then
        log "第 $n 次：配置有问题，不再重试：$msg"
        break
    fi
    if [ "$TRIES" -gt 0 ] && [ "$n" -ge "$TRIES" ]; then
        log "第 $n 次没推上：$msg；已试 $n 次，今天不再试"
        break
    fi
    if [ $(( $(date +%s) + WAIT )) -gt "$DEADLINE" ]; then
        log "第 $n 次没推上：$msg；再试就过了美东当天，今天不再试，下个交易日收盘后再推"
        break
    fi
    log "第 $n 次没推上：$msg；$(date -v+"${WAIT}"S '+%H:%M') 再试"
    sleep "$WAIT"
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then   # Mac 睡着时 sleep 不走，醒来可能已经过了截止时间
        log "醒来已经过了美东当天（Mac 睡着了？），今天不再试，下个交易日收盘后再推"
        break
    fi
done

# 免费签名 7 天过期，过期后 app 打不开，推了也看不到。快到期时在日志里提一句（按最近一次装机构建的描述文件算）。
prof="$ROOT/ios-app/build/dd-device/Build/Products/Debug-iphoneos/FamilyFinance.app/embedded.mobileprovision"
if [ -f "$prof" ]; then
    note="$(security cms -D -i "$prof" 2>/dev/null | python3 -I -c '
import plistlib, sys
from datetime import datetime, timezone
exp = plistlib.loads(sys.stdin.buffer.read())["ExpirationDate"].replace(tzinfo=timezone.utc)
days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400
when = exp.astimezone().strftime("%m-%d %H:%M")
if days < 0:
    print(f"{when} 已经过期，app 打不开了")
elif days < 2:
    print(f"{when} 到期，到时 app 会打不开")' 2>/dev/null)"
    [ -n "$note" ] && log "提醒：手机 app 的免费签名 ${note}，要跑 ios-app/scripts/install-device.sh 重装"
fi
exit 0
