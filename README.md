# 家庭资产与税务（本地）

Last updated: 2026-11-17

一个只在自己电脑上跑的家庭理财工具：追踪 Schwab 股票、爸爸的 AAPL 股票计划、EB-5 和现金的每日盈亏，推算美国税务居民身份，估算卖出时的美国联邦税（可选纽约州/市）和爸爸的中国个税。另有一个只读的 iPhone app（`ios-app/`）。

**仅供估算，不构成税务建议，以 CPA 意见为准。**

这个仓库里没有任何人的真实数据：真实数据只在本机的 `./data/`（不入库）。仓库自带一个虚构家庭的示例数据，可以直接看效果。

## 先看示例

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh --demo
```

然后打开 <http://127.0.0.1:8502>。示例数据由 `core/demo.py` 生成：代码是真实公司的，股数、成本、余额都是编的；价格按日期做确定性的随机游走，不联网；日期都相对今天，每次启动重新生成（放在 `data/demo/`）。

## 用自己的数据

```bash
./run.sh
```

然后打开 <http://127.0.0.1:8501>。第一次运行会把 `./data/` 里的初始数据（`initial_lots.csv`、`initial_data.json`，没有也行）写进 `./data/finance.db`，之后在「数据管理」页里导入 Schwab 的 Lot Details CSV、记现金和转账。

## 隐私

- 所有个人数据只在 `./data/`（已加入 `.gitignore`）。
- `.streamlit/config.toml`：只监听 `127.0.0.1`，关闭 Streamlit 使用统计。`run.sh` 会先进入项目目录，确保这份配置生效。
- 唯一的外网请求是 yfinance：只发送股票代码和 `CNY=X`，不带持仓数量或金额。侧边栏可开「离线模式」，只用缓存和手动价格。

## 目录

| 路径 | 内容 |
|---|---|
| `app.py`、`views/` | Streamlit 页面：资产总览、税务身份、美国联邦税、卖出模拟、优化提示、爸爸的中国个税、数据管理 |
| `core/` | 计算逻辑（与界面无关，可单独测试） |
| `tax_params/<年份>.json` | 所有税率、档位、扣除额；每项带 `source` 来源链接。2027 年是临时值（沿用 2026），IRS 发布后替换 |
| `core/demo.py`、`scripts/make_demo.py` | 虚构家庭的示例数据：`./run.sh --demo` 和手机 app 的展示模式用 |
| `ios-app/` | 只读的 iPhone app 和小组件，见 `ios-app/README.md` |
| `data/` | 个人数据和数据库（不入库） |
| `tests/` | pytest；`test_required.py` 是需求里列出的 8 个用例 |

## 测试

```bash
.venv/bin/python -m pytest -q
```

测试不联网；导入校验用例读取 `./data/expected_totals.json`，没有该文件时跳过。

## 更新税务参数

新的一年：复制上一年的 json，按 IRS Rev. Proc.（或对应法规）逐项更新 `value` 和 `source`，把 `_meta.status` 改为 `official`。`tests/test_extra.py::test_every_param_has_source` 会检查每个参数都带来源链接。
