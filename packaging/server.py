"""打包版「家庭资产」的网站进程：在 127.0.0.1 上跑 Streamlit；所有页面关掉一段时间后自己退出，不常驻。

由 launch.sh 调用，当前目录 = 应用代码目录：python3 server.py family-finance-viewer <端口>
（第一个参数只是给 launch.sh 认进程用的标记。）
"""
import os
import sys
import threading
import time

IDLE_EXIT_SECONDS = 30 * 60      # 浏览器里的页面全关掉 30 分钟后退出；下次双击应用会重新启动


def exit_when_idle() -> None:
    from streamlit.runtime import Runtime

    last_busy = time.monotonic()
    while True:
        time.sleep(20)
        try:
            # 私有属性（Streamlit 1.66）；以后改名了就只是不自动退出
            busy = Runtime.instance()._session_mgr.num_active_sessions() > 0
        except Exception:
            continue
        if busy:
            last_busy = time.monotonic()
        elif time.monotonic() - last_busy > IDLE_EXIT_SECONDS:
            os._exit(0)


def main() -> None:
    port = sys.argv[2]
    threading.Thread(target=exit_when_idle, daemon=True).start()
    from streamlit.web import cli

    sys.argv = [
        "streamlit", "run", "app.py",
        "--server.address", "127.0.0.1",
        "--server.port", port,
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--server.runOnSave", "false",
        "--browser.gatherUsageStats", "false",
    ]
    sys.exit(cli.main())


if __name__ == "__main__":
    main()
