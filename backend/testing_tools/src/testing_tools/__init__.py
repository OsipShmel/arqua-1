import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        "testing_tools.app.main:create_app",
        factory=True,
        host=os.environ.get("TT_HOST", "127.0.0.1"),
        port=int(os.environ.get("TT_PORT", "8000")),
    )
