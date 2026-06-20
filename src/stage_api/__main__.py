"""Console entry point: ``stage-api`` -> uvicorn server."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "stage_api.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
