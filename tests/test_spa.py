from pathlib import Path

import pytest
from django.test import Client, override_settings


def test_spa_route_serves_compiled_index(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>CivicLoop</title>", encoding="utf-8")

    with override_settings(FRONTEND_INDEX=index):
        response = Client().get("/")

    assert response.status_code == 200
    assert b"<title>CivicLoop</title>" in b"".join(response.streaming_content)


def test_unknown_api_route_does_not_fall_back_to_spa() -> None:
    response = Client().get("/api/v1/does-not-exist")

    assert response.status_code == 404


def test_frontend_deep_link_serves_compiled_index(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>CivicLoop</title>", encoding="utf-8")

    with override_settings(FRONTEND_INDEX=index):
        response = Client().get("/campaigns/42")

    assert response.status_code == 200
    assert b"<title>CivicLoop</title>" in b"".join(response.streaming_content)


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/api/v1/does-not-exist", 404),
        ("/admin/does-not-exist", 302),
        ("/assets/does-not-exist.js", 404),
        ("/static/does-not-exist.js", 404),
    ],
)
def test_reserved_namespace_does_not_fall_back_to_spa(path: str, expected_status: int) -> None:
    response = Client().get(path)

    assert response.status_code == expected_status


def test_missing_compiled_index_returns_controlled_404(tmp_path: Path) -> None:
    missing_index = tmp_path / "missing" / "index.html"

    with override_settings(FRONTEND_INDEX=missing_index):
        response = Client().get("/")

    assert response.status_code == 404
    assert str(missing_index).encode() not in response.content


def test_administrator_entry_is_feature_gated_and_serves_separate_bundle(
    tmp_path: Path,
) -> None:
    index = tmp_path / "admin.html"
    index.write_text("<!doctype html><title>CivicLoop administrator</title>", encoding="utf-8")

    disabled = Client().get("/admin/security")
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        ADMIN_FRONTEND_INDEX=index,
    ):
        enabled = Client().get("/admin/security")

    assert disabled.status_code == 404
    assert enabled.status_code == 200
    assert b"CivicLoop administrator" in b"".join(enabled.streaming_content)
