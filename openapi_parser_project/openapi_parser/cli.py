from __future__ import annotations

import argparse

from .parser import OpenAPIParser


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse OpenAPI 3.x contract"
    )

    parser.add_argument(
        "file",
        help="Path to OpenAPI JSON/YAML file",
    )

    args = parser.parse_args()

    openapi_parser = OpenAPIParser()

    contract = openapi_parser.parse(
        args.file
    )

    print(
        contract.model_dump_json(
            indent=2,
            by_alias=True,
        )
    )


if __name__ == "__main__":
    main()