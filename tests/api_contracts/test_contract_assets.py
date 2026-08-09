from pathlib import Path

from django.test import Client, override_settings


def test_openapi_and_schema_assets_are_served_with_safe_media_types(tmp_path: Path) -> None:
    openapi_root = tmp_path / "openapi"
    schema_root = tmp_path / "schemas"
    openapi_root.mkdir()
    schema_root.mkdir()
    (openapi_root / "test.yaml").write_text("openapi: 3.1.1\n", encoding="utf-8")
    (schema_root / "test.json").write_text("{}", encoding="utf-8")

    with override_settings(
        API_CONTRACT_ROOTS={"openapi": openapi_root, "schemas": schema_root}
    ):
        openapi = Client().get("/api/v1/contracts/openapi/test.yaml")
        schema = Client().get("/api/v1/contracts/schemas/test.json")

    assert openapi.status_code == 200
    assert openapi["Content-Type"].startswith("application/yaml")
    assert schema.status_code == 200
    assert schema["Content-Type"].startswith("application/schema+json")


def test_contract_route_rejects_unknown_groups_extensions_and_traversal() -> None:
    client = Client()

    assert client.get("/api/v1/contracts/private/example.yaml").status_code == 404
    assert client.get("/api/v1/contracts/openapi/example.env").status_code == 404
    assert client.get("/api/v1/contracts/openapi/%2e%2e/pyproject.toml").status_code == 404


def test_swagger_page_sets_csrf_cookie(tmp_path: Path) -> None:
    swagger_index = tmp_path / "swagger.html"
    swagger_index.write_text("<!doctype html><div id='swagger-ui'></div>", encoding="utf-8")

    with override_settings(SWAGGER_INDEX=swagger_index):
        response = Client().get("/api/docs")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "csrftoken" in response.cookies
