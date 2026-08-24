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


def test_web_container_is_only_published_on_loopback() -> None:
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text()

    assert '"127.0.0.1:8000:8000"' in compose
    assert '\n      - "8000:8000"' not in compose


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
    assert "--require-admin-identity" in workflow
    assert "--require-admin-integrations" in workflow
    assert "CIVICLOOP_IDENTITY_KEY_FILE: /tmp/civicloop-ci-identity-keyring.json" in workflow
    assert "CIVICLOOP_INTEGRATION_KEY_FILE: /tmp/civicloop-ci-integration-keyring.json" in workflow
    assert 'CIVICLOOP_INTEGRATIONS_ENABLED: "true"' in workflow
    assert "COMPOSE_FILE: compose.yaml:compose.integrations.yaml" in workflow
    assert "Create synthetic administrator integration key" in workflow
    assert "civicloop-compose-integration-keyring.json" in workflow
    assert "test_security_event_database.py" in workflow
    assert "test_credential_concurrency.py" in workflow
    assert "test_rate_limits.py" in workflow
    assert "tests/integrations/test_api.py" in workflow
    assert "sudo chown 10001:10001 /tmp/civicloop-compose-identity-keyring.json" in workflow
    assert "sudo chown 10001:10001 /tmp/civicloop-compose-integration-keyring.json" in workflow
    assert workflow.index(dependencies) < workflow.index(migrate) < workflow.index(runtime)
    assert "if: failure()" in workflow
    assert "docker compose ps -a" in workflow
    assert "docker compose logs" in workflow
    assert "if: always()" in workflow
    assert workflow.index("if: failure()") < workflow.rindex("if: always()")
    assert "docker compose ps --services --status running" in workflow
    assert "grep -Fxq web" in workflow
    assert "grep -Fxq worker" in workflow
    assert "grep -Fxq scheduler" in workflow
    assert "s/replace-with-a-unique-demo-password/civicloop-ci-only-demo-password/" in workflow


def test_integration_runbook_uses_opt_in_compose_override() -> None:
    runbook = (REPOSITORY_ROOT / "docs" / "integrations-administration.md").read_text()

    assert "-f compose.yaml -f compose.integrations.yaml up -d --build" in runbook
    assert "base `compose.yaml` neither\nrequires the host path nor mounts the key" in runbook


def test_production_deployment_is_manual_pinned_and_environment_gated() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "deploy-production.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "commit_sha:" in workflow
    assert "environment:" in workflow
    assert "name: production" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "deploy-production.sh" in workflow


def test_production_deployer_limits_credentials_and_cleans_firewall_rule() -> None:
    deployer = (REPOSITORY_ROOT / ".github" / "scripts" / "deploy-production.sh").read_text()

    assert "^[0-9a-f]{40}$" in deployer
    assert "StrictHostKeyChecking=yes" in deployer
    assert "IdentitiesOnly=yes" in deployer
    assert "trap cleanup EXIT" in deployer
    assert '"port":"22"' in deployer
    assert '"subnet_size":32' in deployer
    assert "SSH_ORIGINAL_COMMAND" not in deployer
    assert 'deploy "$target_commit"' in deployer
