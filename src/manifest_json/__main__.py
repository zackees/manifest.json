"""Allow `python -m manifest_json.cli` and `python -m manifest_json`."""

from manifest_json.cli import validate_main

if __name__ == "__main__":
    raise SystemExit(validate_main())
