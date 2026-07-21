from __future__ import annotations

import uvicorn

from config.network import configure_network_policy


HOST = "127.0.0.1"
PORT = 8766


def main() -> None:
    configure_network_policy()

    uvicorn.run(
        "data_hub.api.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False,
    )


if __name__ == "__main__":
    main()