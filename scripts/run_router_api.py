from __future__ import annotations

import os

import uvicorn

from config.network import configure_network_policy
from config.utf8 import configure_utf8_stdio
from config.version import PROJECT_VERSION


HOST = "127.0.0.1"
PORT = 8765


def main() -> None:
    configure_utf8_stdio()
    os.environ["HERMES_DB_OWNER_PROCESS"] = "router"
    os.environ["HERMES_DB_ENFORCE_OWNER"] = "1"
    configure_network_policy()
    print(
        f"Starting Hermes OPC Agent Router {PROJECT_VERSION} at http://{HOST}:{PORT}",
        flush=True,
    )

    uvicorn.run(
        "router.api.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False,
    )


if __name__ == "__main__":
    main()
