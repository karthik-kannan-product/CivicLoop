from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_collects_static_as_root_and_locks_down_application_files() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()

    collectstatic = "python backend/manage.py collectstatic --noinput"
    lock_down = "chmod -R a-w /app"

    assert "chown -R civicloop:civicloop /app" not in dockerfile
    assert collectstatic in dockerfile
    assert lock_down in dockerfile
    assert dockerfile.index(collectstatic) < dockerfile.index(lock_down) < dockerfile.index(
        "USER civicloop"
    )


def test_compose_app_services_have_a_read_only_root_and_safe_temporary_storage() -> None:
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text()

    assert "read_only: true" in compose
    assert "- /tmp:mode=1777" in compose
    assert compose.count("tmpfs:") == 1
