#!/usr/bin/env bash
# 自动续签：免费 Apple ID 的签名 7 天到期，快到期（或已到期）时重装一次「家庭资产」。
# 由 LaunchAgent com.justaboringname.family-finance-ios-renew 每 10 分钟跑一次；离到期超过
# FF_RENEW_WINDOW_H 小时（默认 12）什么都不做，手机不在同一 Wi-Fi 也什么都不做。不会在手机上打开 app。
#   ios-app/scripts/renew-device.sh [--force]
# 日志：~/Library/Logs/family-finance-ios-renew.log
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

window_h="${FF_RENEW_WINDOW_H:-12}"
app="build/dd-device/Build/Products/Debug-iphoneos/FamilyFinance.app"
state="build/renew-state"   # 记下「重装了但没拿到新签名」的那个到期时间（epoch 秒）
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"家庭资产（iPhone）\"" >/dev/null 2>&1 || true; }

# app 和小组件两份描述文件里较早的到期时间（epoch 秒）；读不到就是 0。
expiry() {
  local min=0 p d e
  for p in "$app/embedded.mobileprovision" "$app"/PlugIns/*.appex/embedded.mobileprovision; do
    [ -f "$p" ] || continue
    d="$(security cms -D -i "$p" 2>/dev/null | plutil -extract ExpirationDate raw -o - - 2>/dev/null)" || continue
    e="$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$d" +%s 2>/dev/null)" || continue
    if [ "$min" -eq 0 ] || [ "$e" -lt "$min" ]; then min="$e"; fi
  done
  echo "$min"
}

now="$(date +%s)"
before="$(expiry)"
if [ "${1:-}" != "--force" ] && [ "$before" -gt 0 ]; then
  [ $((before - now)) -gt $((window_h * 3600)) ] && exit 0
  # 到期前重装过、苹果没给新签名：等这份到期再续。
  if [ -f "$state" ] && [ "$(cat "$state")" = "$before" ] && [ "$now" -lt "$before" ]; then exit 0; fi
fi

list="$(mktemp)"
outf="$(mktemp)"
trap 'rm -f "$list" "$outf"' EXIT
xcrun devicectl list devices --json-output "$list" >/dev/null 2>&1
device="$(python3 -I - "$list" <<'PY'
import json, sys
try:
    devices = json.load(open(sys.argv[1]))["result"]["devices"]
except Exception:
    devices = []
for d in devices:
    hw, cp = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
    if hw.get("reality") == "physical" and hw.get("platform") == "iOS" and cp.get("pairingState") == "paired":
        print(hw["udid"])
        break
PY
)"
if [ -z "$device" ]; then
  say "没找到已配对的 iPhone，下次再试"
  exit 0
fi

# 构建或安装可能卡住（手机走出 Wi-Fi）；卡住的那次会挡住以后每一次，所以最多等 FF_RENEW_TIMEOUT 秒，超时整组 kill。
set -m
FF_RENEW=1 scripts/install-device.sh "$device" >"$outf" 2>&1 &
job=$!
( sleep "${FF_RENEW_TIMEOUT:-900}"; echo "超时" >>"$outf"; kill -- -"$job" 2>/dev/null ) &
dog=$!
wait "$job"; rc=$?
kill -- -"$dog" 2>/dev/null; wait "$dog" 2>/dev/null
set +m
out="$(cat "$outf")"

if [ "$rc" -eq 0 ]; then
  after="$(expiry)"
  until="$(date -r "$after" '+%-m月%-d日 %H:%M')"
  if [ "$after" -gt "$before" ]; then
    rm -f "$state"
    say "已续签，有效期到 $until"
    notify "已续签，有效期到 $until。"
  else
    echo "$after" >"$state"
    say "重装了，但签名没有延长（仍是 $until 到期）；等它到期后再续"
  fi
else
  say "失败：$out"
  # 只有 app 真的已经打不开了才提醒，一天最多两次。
  stamp="build/renew-failed"
  if [ "$now" -ge "$before" ] && { [ ! -f "$stamp" ] || [ $((now - $(stat -f %m "$stamp"))) -gt 43200 ]; }; then
    touch "$stamp"
    notify "续签失败，看 ~/Library/Logs/family-finance-ios-renew.log"
  fi
  exit 1
fi
