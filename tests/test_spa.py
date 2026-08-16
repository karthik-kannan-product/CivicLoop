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
        ("/admin/does-not-exist", 404),
        ("/internal/does-not-exist", 404),
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

    disabled = [Client().get(path) for path in ("/admin/security", "/admin/security/")]
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        ADMIN_FRONTEND_INDEX=index,
    ):
        enabled = [Client().get(path) for path in ("/admin/security", "/admin/security/")]

    assert {response.status_code for response in disabled} == {404}
    assert {response.status_code for response in enabled} == {200}
    assert all("csrftoken" in response.cookies for response in enabled)
    assert all(
        b"CivicLoop administrator" in b"".join(response.streaming_content)
        for response in enabled
    )


@pytest.mark.parametrize(
    ("identity_enabled", "integrations_enabled", "expected_status"),
    [
        (False, False, 404),
        (False, True, 404),
        (True, False, 404),
        (True, True, 200),
    ],
)
def test_integrations_administrator_entry_requires_both_feature_flags(
    tmp_path: Path,
    identity_enabled: bool,
    integrations_enabled: bool,
    expected_status: int,
) -> None:
    index = tmp_path / "admin.html"
    index.write_text("<!doctype html><title>CivicLoop administrator</title>", encoding="utf-8")

    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=identity_enabled,
        CIVICLOOP_INTEGRATIONS_ENABLED=integrations_enabled,
        ADMIN_FRONTEND_INDEX=index,
    ):
        responses = [Client().get(path) for path in ("/admin/integrations", "/admin/integrations/")]

    assert {response.status_code for response in responses} == {expected_status}
    if expected_status == 200:
        assert all("csrftoken" in response.cookies for response in responses)
        assert all(
            b"CivicLoop administrator" in b"".join(response.streaming_content)
            for response in responses
        )


def test_django_admin_is_only_available_at_internal_route() -> None:
    client = Client()

    assert client.get("/admin/").status_code == 404
    assert client.get("/internal/django-admin/").status_code == 302


def test_missing_administrator_bundle_fails_closed_without_path_disclosure(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "private" / "missing-admin.html"

    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        ADMIN_FRONTEND_INDEX=missing,
    ):
        response = Client().get("/admin/security")

    assert response.status_code == 404
    assert str(missing).encode() not in response.content
