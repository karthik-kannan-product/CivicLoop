from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPOSITORY_ROOT / "compose.observability.yaml"
EXPECTED_IMAGE = (
    "arizephoenix/phoenix:version-20.4.0-nonroot"
    "@sha256:5605acbd1f6c7b0f425e52080aed303818f322a46174a8e60332868bbe015b07"
)


def test_phoenix_is_optional_pinned_private_and_resource_bounded() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    phoenix = compose["services"]["phoenix"]

    assert phoenix["image"] == EXPECTED_IMAGE
    assert phoenix["profiles"] == ["observability"]
    assert phoenix["ports"] == ["127.0.0.1:6006:6006"]
    assert "4317" not in repr(phoenix.get("ports", []))
    assert phoenix["read_only"] is True
    assert phoenix["security_opt"] == ["no-new-privileges:true"]
    assert phoenix["environment"]["PHOENIX_ENABLE_AUTH"] == "True"
    assert phoenix["environment"]["PHOENIX_USE_SECURE_COOKIES"] == "True"
    assert phoenix["environment"]["PHOENIX_DEFAULT_RETENTION_POLICY_DAYS"] == "14"
    assert phoenix["volumes"] == ["phoenix-data:/data"]
    assert phoenix["deploy"]["resources"]["limits"] == {
        "cpus": "0.75",
        "memory": "768M",
    }


def test_app_exports_over_internal_http_without_readiness_dependency() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text())

    for service_name in ("web", "worker"):
        service = compose["services"][service_name]
        assert service["environment"]["CIVICLOOP_TELEMETRY_ENABLED"] == "true"
        assert service["environment"]["CIVICLOOP_TELEMETRY_ENDPOINT"] == (
            "http://phoenix:6006/v1/traces"
        )
        assert service["environment"]["CIVICLOOP_TELEMETRY_HEADERS_FILE"] == (
            "/run/secrets/phoenix-otlp-headers"
        )
        host_path = "${CIVICLOOP_PHOENIX_OTLP_HEADERS_HOST_PATH:"
        assert service["volumes"] == [
            f"{host_path}?Set an absolute host OTLP headers path}}:"
            "/run/secrets/phoenix-otlp-headers:ro"
        ]
        assert "phoenix" not in service.get("depends_on", {})

    assert "phoenix-data" in compose["volumes"]


def test_base_compose_remains_phoenix_free_and_independently_ready() -> None:
    base = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text())

    assert "phoenix" not in base["services"]
    assert base["services"]["web"]["healthcheck"]
    assert all(
        "phoenix" not in service.get("depends_on", {})
        for service in base["services"].values()
    )
