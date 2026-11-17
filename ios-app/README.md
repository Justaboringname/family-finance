# 家庭资产（iPhone）

Last updated: 2026-11-17

「家庭资产与税务」的只读 iPhone app，界面是网页同一套「钞版」：总览（家族净资产的凭证，构成、走势、流动性、集中度）、
持仓（点开看明细）、日程（IPO 这类有关键节点的计划是打孔息票，带倒计时），加主屏小号、中号小组件。

**app 里没有任何联网代码。** 数据是 Mac 上导出的快照（`scripts/export_snapshot.py`，用网站缓存的行情，数字和网页同一套格式化），
只会通过下面几种方式到手机上：

| 怎么更新 | 命令 / 做法 |
| --- | --- |
| 重新装机（顺带更新数据） | `ios-app/scripts/install-device.sh` |
| 每个工作日收盘后自动推 | 收盘任务记完当天快照后跑 `scripts/push_phone_after_close.sh`。手机不在同一 Wi-Fi 时，每 25 分钟重试一次，到美东当天结束为止。结果写在 `~/Library/Logs/family-finance-after-close.log`，行首带「[手机]」 |
| 手动只推数据，不重装 | `ios-app/scripts/push-snapshot.sh`：走 Xcode 的设备配对，写进 app 共享目录里的 `Library/Application Support/snapshot.json`，推完会核对手机上的文件。devicectl 写不进共享目录的根目录，而且不会报错。 |
| 隔空投送 | `.venv/bin/python scripts/export_snapshot.py --downloads`，把 `~/Downloads/家庭资产快照-日期.json` 发到手机存进「文件」，app 右上角点导入 |

生成的快照含个人数据，只在 `ios-app/Generated/`（gitignore）和手机上。

## 展示模式

给别人看 app 时不想露出真实数字：点左上角的标志，整个 app 和小组件换成一个虚构家庭的示例数据，标志旁边出现「示例数据」。
再点一下退出，要先过面容 ID / 密码（手机在别人手里时，对方点一下不该看到真实数据）。开关存在 App Group 里，重装也还在。

示例数据是 `core/demo.py` 生成的（`scripts/make_demo.py --snapshot`），和真快照同一个格式，多一个 `demo` 标记：
`install-device.sh` 装机时打包一份，每天收盘后的推送也顺手推一份，所以日期总是新的。没有自己的数据也能用它看 app 长什么样。
模拟器里用启动参数 `-FFDemo 1` 直接进展示模式。

## 免费签名自动续

免费 Apple ID 的签名 7 天过期。`scripts/renew-device.sh` 给 LaunchAgent 每 10 分钟跑一次：读构建产物里描述文件的到期时间，
到期前 12 小时内和到期之后才动手，重装一遍（不在手机上打开 app）。实测签名还有效时重装拿到的是同一份描述文件，
所以真正续上是在到期后、手机和 Mac 在同一 Wi-Fi 时的第一次运行。

## 构建

- Xcode 27、iOS 26+，[XcodeGen](https://github.com/yonaskolb/XcodeGen)（`brew install xcodegen`）。`.xcodeproj` 是生成的，不入库。
- 个人签名在 gitignore 的 `Config/Local.xcconfig`（照 `Local.example.xcconfig` 填）：免费 Apple ID 的个人团队就行，
  App Group 免费团队也能用。免费签名 7 天过期，过期后重跑 `install-device.sh`。
- 测试：`xcodebuild test -project FamilyFinance.xcodeproj -scheme FamilyFinance -destination 'platform=iOS Simulator,name=iPhone 18 Pro Max'`。
  用的是合成快照 `Tests/Fixtures/snapshot-synthetic.json`（`.venv/bin/python ios-app/scripts/make_fixture.py` 生成，不含真实数据）；
  改了 `core/mobile.py` 的格式要重新生成。
- 模拟器截图：启动参数 `-FFTab 1` / `2` 直接打开持仓 / 日程，`-FFWidgetPreview 1` 在 app 里按实际尺寸画小组件。

## 目录

| 路径 | 内容 |
| --- | --- |
| `Sources/Shared/` | 快照模型和读取（`Snapshot.swift`、`SnapshotStore.swift`）、令牌（`Theme.swift`）、雕版纹样（`Guilloche.swift`，和网页同一套数学）、小组件界面 |
| `Sources/App/` | 三个页面、导入、给家人的分享长图（`ShareReport.swift`） |
| `Sources/Widgets/` | 小组件的时间线（一小时看一次共享目录里有没有新快照） |
| `Resources/AppIcon.icon` | app 图标「打孔息票」：Icon Composer 文档，由 `scripts/make_app_icon.py` 生成，别手改 |
