#!/usr/bin/env bash
# 导出最新快照 → 生成工程 → 构建 → 装到已配对的 iPhone 并打开。
#   ios-app/scripts/install-device.sh [设备 udid]
# 免费 Apple ID 签的包 7 天后打不开，到时重跑这个脚本（顺便把数据更新到最新）。
# 需要：Config/Local.xcconfig 填好 DEVELOPMENT_TEAM / FF_BUNDLE_PREFIX，Xcode 里登录了 Apple ID，
# iPhone 已配对（有线或无线）并开了开发者模式。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"

command -v xcodegen >/dev/null || { echo "缺 XcodeGen：brew install xcodegen" >&2; exit 1; }
[ -f Config/Local.xcconfig ] || { echo "先把 Config/Local.example.xcconfig 复制成 Config/Local.xcconfig 并填好。" >&2; exit 1; }

# FF_RENEW=1（自动续签用）：不在手机上打开 app；快照导不出来（行情缓存不全）时沿用上一份，签名不能因此断掉。
if ! "$ROOT/.venv/bin/python" "$ROOT/scripts/export_snapshot.py" --out Generated/snapshot.json; then
  [ -n "${FF_RENEW:-}" ] && [ -s Generated/snapshot.json ] || exit 1
  echo "快照没导出来，沿用上一份。" >&2
fi
# 展示模式用的示例数据（虚构家庭，日期跟着今天走）。生成不了不拦着装机。
"$ROOT/.venv/bin/python" "$ROOT/scripts/make_demo.py" --snapshot Generated/demo-snapshot.json >/dev/null \
  || echo "示例数据没生成：这次装的 app 展示模式用的是上一份，或者没有展示模式。" >&2

device="${1:-}"
if [ -z "$device" ]; then
  list="$(mktemp)"
  trap 'rm -f "$list"' EXIT
  xcrun devicectl list devices --json-output "$list" >/dev/null
  device="$(python3 -I - "$list" <<'PY'
import json, sys
for d in json.load(open(sys.argv[1]))["result"]["devices"]:
    hw = d.get("hardwareProperties", {})
    if hw.get("reality") == "physical" and hw.get("platform") == "iOS" and d.get("connectionProperties", {}).get("pairingState") == "paired":
        print(hw["udid"])
        break
PY
)"
fi
[ -n "$device" ] || { echo "没找到已配对的 iPhone（解锁手机、检查连线或同一 Wi-Fi）。" >&2; exit 1; }

xcodegen -q
log="$(mktemp)"
if ! xcodebuild build -project FamilyFinance.xcodeproj -scheme FamilyFinance \
  -destination "platform=iOS,id=$device" -derivedDataPath build/dd-device \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration >"$log" 2>&1; then
  grep -E "error:|BUILD FAILED" "$log" | sort -u >&2
  echo "完整日志：$log" >&2
  exit 1
fi
rm -f "$log"

app="build/dd-device/Build/Products/Debug-iphoneos/FamilyFinance.app"
xcrun devicectl device install app --device "$device" "$app" >/dev/null
if [ -n "${FF_RENEW:-}" ]; then
  echo "已装好「家庭资产」。"
  exit 0
fi
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Info.plist")"
if xcrun devicectl device process launch --device "$device" "$bundle_id" >/dev/null 2>&1; then
  echo "已装好并在 iPhone 上打开「家庭资产」。"
else
  echo "已装好。iPhone 现在打不开它：先解锁手机；第一次装还要在「设置 → 通用 → VPN 与设备管理」里信任开发者，再打开「家庭资产」。"
fi
