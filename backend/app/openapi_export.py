"""Write or check the OpenAPI spec: `python -m app.openapi_export --out PATH [--check]`."""

import argparse
import json
import sys
from pathlib import Path

from app.config import Settings
from app.main import create_app


def render_spec() -> str:
    """The app's OpenAPI document as stable, pretty-printed JSON."""
    # Skip .env and the static mount: the spec documents API shapes, which must not vary
    # per deployment. Runtime config still comes from env vars (tests/unit/test_openapi_export.py).
    spec = create_app(Settings(_env_file=None, frontend_dir="")).openapi()
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="spec file, e.g. docs/openapi.json")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the file differs from the app's spec"
    )
    args = parser.parse_args(argv)

    spec = render_spec()
    out: Path = args.out
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current != spec:
            reason = "is missing" if current is None else "is out of date"
            print(
                f"OpenAPI drift: {out} {reason} compared with the app. Run `make openapi`.",
                file=sys.stderr,
            )
            return 1
        print(f"{out} is up to date.")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(spec, encoding="utf-8")
    print(f"Wrote {out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
