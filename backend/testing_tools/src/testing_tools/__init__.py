import logging

import uvicorn

from testing_tools.app.settings import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    uvicorn.run(
        "testing_tools.app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
