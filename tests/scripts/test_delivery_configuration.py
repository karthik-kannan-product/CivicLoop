from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_ci_backend_uses_runner_postgres_and_valkey_services() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "DATABASE_URL: sqlite:///:memory:" not in workflow
    assert "DATABASE_URL: postgresql://civicloop_ci:" in workflow
    assert "CELERY_BROKER_URL: redis://localhost:6379/1" in workflow


def test_runtime_image_and_readme_keep_readiness_inside_containers() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()
    readme = (REPOSITORY_ROOT / "README.md").read_text()

    assert "COPY scripts/ /app/scripts/" in dockerfile
    assert "docker compose up -d --build --wait --wait-timeout 120" in readme
    assert (
        "docker compose exec web python scripts/readiness.py --base-url http://localhost:8000"
        in readme
    )
    assert "python .\\scripts\\readiness.py" not in readme
