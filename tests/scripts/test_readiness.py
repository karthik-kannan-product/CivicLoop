import json
from unittest.mock import Mock, patch

import pytest
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


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_returns_zero_when_both_endpoints_pass(
    mock_open: Mock, _mock_monotonic: Mock
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test"]) == 0


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_can_require_administrator_identity(
    mock_open: Mock, _mock_monotonic: Mock
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
        FakeResponse({"stage": "anonymous"}),
    ]

    assert main(
        ["--base-url", "http://civicloop.test", "--require-admin-identity"]
    ) == 0
    assert mock_open.call_args_list[-1].args[0] == (
        "http://civicloop.test/api/v1/admin/security/status"
    )


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_rejects_invalid_administrator_stage(
    mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
        FakeResponse({"stage": "synthetic-invalid"}),
    ]

    assert main(["--require-admin-identity"]) == 1
    assert capsys.readouterr().out == (
        "CivicLoop is reachable but administrator identity is not ready.\n"
    )


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen", side_effect=OSError("connection refused: synthetic-secret"))
def test_readiness_returns_one_without_printing_network_error(
    _mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    assert main(["--base-url", "http://civicloop.test"]) == 1
    output = capsys.readouterr().out

    assert "connection refused" not in output
    assert "synthetic-secret" not in output
    assert output == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_returns_one_when_a_status_is_not_ready(
    mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "not_ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is reachable but not ready.\n"


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_rejects_non_object_json(
    mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.side_effect = [FakeResponse(["not", "an", "object"])]

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.monotonic", return_value=100.0)
@patch("scripts.readiness.urlopen")
def test_readiness_rejects_malformed_json(
    mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.return_value = MalformedResponse()

    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert capsys.readouterr().out == "CivicLoop is not reachable or not ready.\n"


@patch("scripts.readiness.monotonic", side_effect=[100.0, 100.5, 102.0])
@patch("scripts.readiness.urlopen")
def test_readiness_shares_deadline_budget_and_strips_trailing_slash(
    mock_open: Mock, _mock_monotonic: Mock
) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test/"]) == 0
    assert [call.args[0] for call in mock_open.call_args_list] == [
        "http://civicloop.test/api/v1/health/live",
        "http://civicloop.test/api/v1/health/ready",
    ]
    assert [call.kwargs["timeout"] for call in mock_open.call_args_list] == [4.5, 3.0]


@patch("scripts.readiness.monotonic", side_effect=[100.0, 104.0, 105.0])
@patch("scripts.readiness.urlopen")
def test_readiness_stops_before_second_request_when_deadline_is_exhausted(
    mock_open: Mock, _mock_monotonic: Mock, capsys: CaptureFixture[str]
) -> None:
    mock_open.return_value = FakeResponse({"status": "ok"})

    assert main(["--base-url", "http://civicloop.test", "--timeout", "5"]) == 1
    assert mock_open.call_count == 1
    assert mock_open.call_args.kwargs["timeout"] == 1.0
    assert capsys.readouterr().out == "CivicLoop is not reachable or not ready.\n"


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf", "not-a-number"])
def test_readiness_rejects_non_finite_or_non_positive_timeout_without_network_call(
    timeout: str, capsys: CaptureFixture[str]
) -> None:
    with patch("scripts.readiness.urlopen") as mock_open:
        assert main(["--timeout", timeout]) == 1

    output = capsys.readouterr().out

    assert output == "CivicLoop readiness arguments are invalid.\n"
    mock_open.assert_not_called()
