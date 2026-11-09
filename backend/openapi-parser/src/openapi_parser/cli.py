from __future__ import annotations

import argparse
import json

from .loader import OpenAPILoadError
from .parser import OpenAPIParseError, OpenAPIParser


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse OpenAPI 3.x contract (file or URL)"
    )

    parser.add_argument(
        "file",
        help="Path or URL to OpenAPI JSON/YAML file",
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print compact summary for LLM instead of full contract",
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print only contract statistics",
    )

    args = parser.parse_args()

    openapi_parser = OpenAPIParser()

    try:
        contract = openapi_parser.parse(args.file)
    except (OpenAPILoadError, OpenAPIParseError) as exc:
        raise SystemExit(f"Ошибка разбора контракта: {exc}") from exc

    if args.stats:
        print(contract.stats.as_text())
        return

    if args.summary:
        print(json.dumps(contract.summary, ensure_ascii=False, indent=2, default=str))
        return

    print(contract.stats.as_text())
    print("\n--- Contract JSON ---")
    print(
        contract.model_dump_json(
            indent=2,
            by_alias=True,
        )
    )


if __name__ == "__main__":
    main()