from __future__ import annotations

import os

import uvicorn

from config.network import configure_network_policy
from config.utf8 import configure_utf8_stdio
from config.version import PROJECT_VERSION


HOST = "127.0.0.1"
PORT = 8766


def main() -> None:
    configure_utf8_stdio()
    os.environ["HERMES_DB_ENFORCE_OWNER"] = "1"
    os.environ.pop("HERMES_DB_OWNER_PROCESS", None)
    configure_network_policy()
    print(
        f"Starting Hermes OPC Data Hub {PROJECT_VERSION} at http://{HOST}:{PORT}",
        flush=True,
    )

    uvicorn.run(
        "data_hub_proxy_app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False,
    )


if __name__ == "__main__":
    main()
