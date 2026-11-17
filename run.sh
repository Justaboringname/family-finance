#!/bin/bash
# 启动本地理财工具：在项目目录里运行，确保读取 .streamlit/config.toml（只监听 127.0.0.1、不上报统计）
#   ./run.sh           用 ./data 里你自己的数据，http://127.0.0.1:8501
#   ./run.sh --demo    用一个虚构家庭的示例数据（见 core/demo.py，每次启动重新生成，离线），http://127.0.0.1:8502
cd "$(dirname "$0")" || exit 1
if [ "${1:-}" = "--demo" ]; then
    shift
    export FAMILY_FINANCE_DB="$PWD/data/demo/finance.db"
    .venv/bin/python scripts/make_demo.py --db "$FAMILY_FINANCE_DB" || exit 1
    exec .venv/bin/streamlit run app.py --server.port 8502 "$@"
fi
exec .venv/bin/streamlit run app.py "$@"
