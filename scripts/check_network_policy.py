from __future__ import annotations

import os
from urllib.request import proxy_bypass

from config.network import configure_network_policy


def main() -> None:
    value = configure_network_policy()
    host = "push2his.eastmoney.com"

    print(f"NO_PROXY: {value}")
    print(f"东财域名绕过代理: {proxy_bypass(host)}")

    if not proxy_bypass(host):
        raise RuntimeError("东财域名仍未被判定为直连")

    if os.environ.get("NO_PROXY") != os.environ.get("no_proxy"):
        raise RuntimeError("NO_PROXY 大小写变量内容不一致")

    print("网络直连策略检查通过")


if __name__ == "__main__":
    main()