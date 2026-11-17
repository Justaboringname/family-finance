#!/usr/bin/env bash
# 不重新装机，只把最新快照推到 iPhone 上 app 的 App Group 共享目录（走 Xcode 的设备配对，不经过任何服务器）。
#   ios-app/scripts/push-snapshot.sh [设备 udid]        也可以用环境变量 FF_PHONE_UDID 指定设备
#
# 写到共享目录里的 Library/Application Support/snapshot.json。devicectl 写不进共享目录的根目录：
# 根目录已有同名文件时它照样报成功，其实没写（2026-11-11 实测），所以推完要核对手机上那份文件的大小和修改时间。
# app 和小组件从这里读；签名过期（7 天）后 app 打不开，要跑 install-device.sh。
#
# 退出码：0 推好了并核对过；1 配置问题；2 数据不完整没导出；3 没找到已配对的 iPhone；
#         4 推送失败或超时（手机不在同一 Wi-Fi、关机等）；5 devicectl 报成功但手机上的文件没变
set -u
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
DEST="Library/Application Support/snapshot.json"
LIMIT="${FF_PUSH_TIMEOUT:-90}"   # 每条 devicectl 最多等几秒，超时就 kill（系统没有 timeout 命令）
die() { echo "$2" >&2; exit "$1"; }

group="$(sed -n 's/^FF_BUNDLE_PREFIX *= *//p' Config/Local.xcconfig 2>/dev/null | tr -d '[:space:]')"
[ -n "$group" ] || die 1 "Config/Local.xcconfig 里没有 FF_BUNDLE_PREFIX"
DEVICECTL="${FF_DEVICECTL:-$(xcrun -f devicectl 2>/dev/null)}"   # 直接跑 devicectl 本体，超时时 kill 得到它
[ -x "$DEVICECTL" ] || die 1 "找不到 devicectl（要装 Xcode）"

tmp="$(mktemp -d)"
trap 'rm -f "$tmp"/*; rmdir "$tmp"' EXIT

# 跑一条命令，最多等 LIMIT 秒。超时返回 124。
run_limited() {
  "$@" &
  local pid=$! dog rc
  ( sleep "$LIMIT"; kill "$pid" 2>/dev/null ) &
  dog=$!
  wait "$pid"; rc=$?
  if kill "$dog" 2>/dev/null; then wait "$dog" 2>/dev/null; else rc=124; fi   # 看门狗已经自己退出 = 它 kill 过命令
  return "$rc"
}

# 1. 导出（用网站缓存的行情，不联网）。不写 Generated/：那份是装机打包用的，别和正在跑的构建抢。
"$ROOT/.venv/bin/python" "$ROOT/scripts/export_snapshot.py" --out "$tmp/snapshot.json" >"$tmp/export.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  msg="$(tr '\n' ' ' < "$tmp/export.log" | sed 's/  */ /g; s/ $//')"
  [ $rc -eq 2 ] && die 2 "$msg"
  die 1 "导出快照出错：$(tail -1 "$tmp/export.log")"
fi
label="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["as_of_label"])' "$tmp/snapshot.json")"

# 2. 找手机：参数 > FF_PHONE_UDID > 第一台已配对的 iOS 真机
device="${1:-${FF_PHONE_UDID:-}}"
if [ -z "$device" ]; then
  run_limited "$DEVICECTL" list devices --timeout 30 --json-output "$tmp/devices.json" -q >/dev/null 2>&1
  device="$(python3 -I - "$tmp/devices.json" <<'PY' 2>/dev/null
import json, sys
for d in json.load(open(sys.argv[1]))["result"]["devices"]:
    hw = d.get("hardwareProperties", {})
    if hw.get("reality") == "physical" and hw.get("platform") == "iOS" and d.get("connectionProperties", {}).get("pairingState") == "paired":
        print(hw["udid"])
        break
PY
)"
fi
[ -n "$device" ] || die 3 "没找到已配对的 iPhone。"

# 3. 推送。手机的连接隧道断着也没关系，devicectl 会自己重连（同一 Wi-Fi 下一两秒）。
run_limited "$DEVICECTL" device copy to --timeout $((LIMIT > 20 ? LIMIT - 10 : LIMIT)) --device "$device" \
  --source "$tmp/snapshot.json" --destination "$DEST" \
  --domain-type appGroupDataContainer --domain-identifier "group.$group" \
  --json-output "$tmp/copy.json" -q >"$tmp/copy.log" 2>&1
rc=$?
if [ $rc -eq 124 ]; then
  die 4 "等了 ${LIMIT} 秒 iPhone 还没响应（不在同一 Wi-Fi、关机，或锁屏太久？）"
elif [ $rc -ne 0 ]; then
  why="$(grep -m1 'ERROR:' "$tmp/copy.log" | sed 's/^ *ERROR: *//')"
  die 4 "推送失败：${why:-devicectl 退出码 $rc}"
fi

# 4. 核对：手机上那份的大小和修改时间要和刚导出的一致（devicectl 会保留源文件的修改时间）。
python3 -I - "$tmp/copy.json" "$tmp/snapshot.json" <<'PY' || die 5 "devicectl 报成功，但手机上的快照没变（写进的位置不对？）"
import json, os, sys
from datetime import datetime
meta = json.load(open(sys.argv[1]))["result"]["file"]["metadata"]
st = os.stat(sys.argv[2])
when = datetime.fromisoformat(meta["lastModDate"].replace("Z", "+00:00")).timestamp()
sys.exit(0 if int(meta["size"]) == st.st_size and abs(when - st.st_mtime) < 2 else 1)
PY

# 5. 顺手把展示模式的示例数据也推一份（虚构家庭，日期跟着今天走），和真快照放在同一个目录。推不上不算失败。
if "$ROOT/.venv/bin/python" "$ROOT/scripts/make_demo.py" --snapshot "$tmp/demo-snapshot.json" >/dev/null 2>&1; then
  run_limited "$DEVICECTL" device copy to --timeout $((LIMIT > 20 ? LIMIT - 10 : LIMIT)) --device "$device" \
    --source "$tmp/demo-snapshot.json" --destination "${DEST%/*}/demo-snapshot.json" \
    --domain-type appGroupDataContainer --domain-identifier "group.$group" -q >/dev/null 2>&1 || true
fi

echo "已把「${label}」的快照推到 iPhone。"
