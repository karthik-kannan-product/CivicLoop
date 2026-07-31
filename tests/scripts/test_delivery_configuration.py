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
    assert "docker compose up -d --build" in readme
    assert "--wait" not in readme
    assert (
        "docker compose exec web python scripts/readiness.py --base-url http://localhost:8000"
        in readme
    )
    assert "python .\\scripts\\readiness.py" not in readme


def test_compose_ci_stages_dependencies_migration_and_runtime_with_diagnostics() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    dependencies = "docker compose up -d --wait --wait-timeout 120 db valkey"
    migrate = "docker compose up --no-deps --exit-code-from migrate migrate"
    runtime = "docker compose up -d --no-deps web worker scheduler"
    readiness = "docker compose exec -T web python scripts/readiness.py"

    assert dependencies in workflow
    assert migrate in workflow
    assert runtime in workflow
    assert readiness in workflow
    assert workflow.index(dependencies) < workflow.index(migrate) < workflow.index(runtime)
    assert "if: failure()" in workflow
    assert "docker compose ps -a" in workflow
    assert "docker compose logs" in workflow
    assert "if: always()" in workflow
    assert workflow.index("if: failure()") < workflow.index("if: always()")
    assert "docker compose ps --services --status running" in workflow
    assert "grep -Fxq web" in workflow
    assert "grep -Fxq worker" in workflow
    assert "grep -Fxq scheduler" in workflow
