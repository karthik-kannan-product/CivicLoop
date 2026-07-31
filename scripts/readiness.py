import argparse
import json
import sys
from collections.abc import Sequence
from urllib.request import urlopen

REQUEST_TIMEOUT_SECONDS = 5
HEALTH_PATH = "/api/v1/health"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check CivicLoop readiness")
    parser.add_argument("--base-url", default="http://localhost:8000")
    return parser.parse_args(argv)


def fetch_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        decoded: object = json.loads(response.read())

    if not isinstance(decoded, dict):
        raise ValueError("Health endpoint returned a non-object JSON value")

    return {str(key): value for key, value in decoded.items()}


def health_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}{HEALTH_PATH}/{endpoint}"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else [])
    try:
        base_url = str(args.base_url)
        live = fetch_json(health_url(base_url, "live"))
        ready = fetch_json(health_url(base_url, "ready"))
    except Exception:
        print("CivicLoop is not reachable or not ready.")
        return 1

    if live.get("status") != "ok" or ready.get("status") != "ready":
        print("CivicLoop is reachable but not ready.")
        return 1

    print("CivicLoop is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
