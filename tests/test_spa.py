from pathlib import Path

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
