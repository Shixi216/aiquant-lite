from __future__ import annotations

import argparse
import os
import socket

import uvicorn

from config.network import configure_network_policy
from config.utf8 import configure_utf8_stdio
from config.version import PROJECT_VERSION

DEFAULT_HOST = os.environ.get("WEBUI_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("WEBUI_PORT", "8767"))


def _get_lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip not in ips:
                ips.append(ip)
        except Exception:
            pass
    return ips


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes OPC Web UI")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to bind (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    configure_utf8_stdio()
    # Stay a provable non-owner: opening the main DuckDB here must fail fast.
    os.environ["HERMES_DB_ENFORCE_OWNER"] = "1"
    os.environ.pop("HERMES_DB_OWNER_PROCESS", None)
    configure_network_policy()

    lan_ips = _get_lan_ips()
    print("=" * 62, flush=True)
    print(f"  Hermes OPC 投研工作台 (Web UI) v{PROJECT_VERSION}", flush=True)
    print(f"  - 本机访问:     http://127.0.0.1:{args.port}", flush=True)
    if args.host in ("0.0.0.0", "::"):
        for ip in lan_ips:
            print(f"  - 局域网访问:   http://{ip}:{args.port}", flush=True)
        print("  - 请仅在可信局域网内使用；远程访问需要另行配置认证与网络控制。", flush=True)
    else:
        print(f"  - 绑定地址:     http://{args.host}:{args.port}", flush=True)
    print("=" * 62, flush=True)

    uvicorn.run(
        "webui.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=True,
        reload=False,
    )


if __name__ == "__main__":
    main()
