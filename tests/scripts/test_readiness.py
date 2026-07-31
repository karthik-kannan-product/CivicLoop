import json
from unittest.mock import Mock, patch

from pytest import CaptureFixture

from scripts.readiness import main


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class MalformedResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__(None)

    def read(self) -> bytes:
        return b"not-json"


@patch("scripts.readiness.urlopen")
def test_readiness_returns_zero_when_both_endpoints_pass(mock_open: Mock) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test"]) == 0


@patch("scripts.readiness.urlopen", side_effect=OSError("connection refused: synthetic-secret"))
def test_readiness_returns_one_without_printing_network_error(
    _mock_open: Mock, capsys: CaptureFixture[str]
) -> None:
    assert main(["--base-url", "http://civicloop.test"]) == 1
    output = capsys.readouterr().out

    assert "connection refused" not in output
    assert "synthetic-secret" not in output
    assert output == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.urlopen")
def test_readiness_returns_one_when_a_status_is_not_ready(
    mock_open: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "not_ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is reachable but not ready.\n"


@patch("scripts.readiness.urlopen")
def test_readiness_rejects_non_object_json(mock_open: Mock, capsys: CaptureFixture[str]) -> None:
    mock_open.side_effect = [FakeResponse(["not", "an", "object"])]

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.urlopen")
def test_readiness_rejects_malformed_json(mock_open: Mock, capsys: CaptureFixture[str]) -> None:
    mock_open.return_value = MalformedResponse()

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.urlopen")
def test_readiness_strips_trailing_slash_from_base_url(mock_open: Mock) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test/"]) == 0
    assert [call.args[0] for call in mock_open.call_args_list] == [
        "http://civicloop.test/api/v1/health/live",
        "http://civicloop.test/api/v1/health/ready",
    ]
    assert all(call.kwargs["timeout"] == 5 for call in mock_open.call_args_list)
