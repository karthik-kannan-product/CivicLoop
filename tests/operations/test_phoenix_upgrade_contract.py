from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY_COMPOSE = REPOSITORY_ROOT / "compose.observability.yaml"
TARGET_IMAGE = (
    "docker.io/arizephoenix/phoenix:version-20.11.0-nonroot"
    "@sha256:fa710d5910700c2cf682a2bdd34f14593d7357638b0a4415703bbd360fbdafc3"
)
ROLLBACK_IMAGE = (
    "docker.io/arizephoenix/phoenix:version-20.4.0-nonroot"
    "@sha256:5605acbd1f6c7b0f425e52080aed303818f322a46174a8e60332868bbe015b07"
)


def test_phoenix_upgrade_preserves_the_reviewed_non_blocking_runtime_contract() -> None:
    compose = yaml.safe_load(OBSERVABILITY_COMPOSE.read_text(encoding="utf-8"))
    phoenix = compose["services"]["phoenix"]

    assert compose["x-phoenix-image-lock"] == {
        "target": TARGET_IMAGE,
        "rollback": ROLLBACK_IMAGE,
        "activation_allowed": False,
    }
    assert phoenix["image"] == f"${{PHOENIX_IMAGE:-{ROLLBACK_IMAGE}}}"
    assert phoenix["profiles"] == ["observability"]
    assert phoenix["ports"] == ["127.0.0.1:6006:6006"]
    assert phoenix["environment"]["PHOENIX_ENABLE_AUTH"] == "True"
    assert phoenix["environment"]["PHOENIX_DEFAULT_RETENTION_POLICY_DAYS"] == "14"
    assert phoenix["read_only"] is True
    assert phoenix["volumes"] == ["phoenix-data:/data"]
    assert phoenix["deploy"]["resources"]["limits"]["memory"] == "768M"

    for service_name in ("web", "worker"):
        service = compose["services"][service_name]
        assert service["environment"]["CIVICLOOP_TELEMETRY_ENDPOINT"] == (
            "http://phoenix:6006/v1/traces"
        )
        assert service["environment"]["CIVICLOOP_TELEMETRY_HEADERS_FILE"] == (
            "/run/secrets/phoenix-otlp-headers"
        )
        assert "phoenix" not in service.get("depends_on", {})

    assert all(
        not str(port).endswith((":4317", ":4318"))
        for service in compose["services"].values()
        for port in service.get("ports", [])
    )
    assert compose["volumes"]["phoenix-data"] == {
        "name": "${PHOENIX_VOLUME_NAME:?Set the exact existing Phoenix volume name}",
        "external": True,
    }
