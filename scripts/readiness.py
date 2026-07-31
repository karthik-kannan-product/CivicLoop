import argparse
import json
import sys
from collections.abc import Sequence
from time import monotonic
from typing import NoReturn
from urllib.request import urlopen

DEFAULT_REQUEST_DEADLINE_SECONDS = 5.0
HEALTH_PATH = "/api/v1/health"


class ReadinessArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ValueError("invalid readiness arguments")


def positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be positive") from error

    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")

    return timeout


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = ReadinessArgumentParser(description="Check CivicLoop readiness")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--timeout", type=positive_timeout, default=DEFAULT_REQUEST_DEADLINE_SECONDS
    )
    return parser.parse_args(argv)


def fetch_json(url: str, timeout: float) -> dict[str, object]:
    with urlopen(url, timeout=timeout) as response:
        decoded: object = json.loads(response.read())

    if not isinstance(decoded, dict):
        raise ValueError("Health endpoint returned a non-object JSON value")

    return {str(key): value for key, value in decoded.items()}


def health_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}{HEALTH_PATH}/{endpoint}"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv if argv is not None else [])
    except ValueError:
        print("CivicLoop readiness arguments are invalid.")
        return 1

    try:
        base_url = str(args.base_url)
        deadline = monotonic() + float(args.timeout)
        live_timeout = deadline - monotonic()
        if live_timeout <= 0:
            raise TimeoutError
        live = fetch_json(health_url(base_url, "live"), live_timeout)
        ready_timeout = deadline - monotonic()
        if ready_timeout <= 0:
            raise TimeoutError
        ready = fetch_json(health_url(base_url, "ready"), ready_timeout)
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
