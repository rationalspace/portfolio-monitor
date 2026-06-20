"""Entry point: ``portfolio-monitor-second-opinion``.

Runs the Second Opinion FastAPI server bound to localhost only.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "portfolio_monitor.second_opinion.server:app",
        host="127.0.0.1",
        port=8765,
    )


if __name__ == "__main__":
    main()
