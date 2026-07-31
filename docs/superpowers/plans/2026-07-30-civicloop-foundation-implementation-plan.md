# CivicLoop Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a runnable, tested CivicLoop application foundation with Django, React, PostgreSQL, Valkey, Celery process modes, health checks, Docker Compose, and continuous integration.

**Architecture:** Build a modular-monolith repository whose single production image contains the Django application and compiled React frontend and can start in `web`, `worker`, or `scheduler` mode. PostgreSQL is the durable store, Valkey is an ephemeral cache/broker, and the first increment exposes only the application shell and operational health contracts; identity and LaunchLoop behavior remain separate later increments.

**Tech Stack:** Python 3.11, Django 5.2 LTS, Celery 5.6, PostgreSQL 17, Valkey 8, React, TypeScript, Vite, Vitest, pytest, uv, Docker Compose, GitHub Actions

## Global Constraints

- One nonprofit organization per deployment.
- Invite-only users and application authentication are out of scope for this foundation increment.
- The repository and all new first-party code remain MIT licensed.
- No proprietary SaaS or credential is required to build, test, or run this increment.
- The same CivicLoop image must support `web`, `worker`, and `scheduler` process modes.
- PostgreSQL is the only durable application store; Valkey must never contain the only copy of accepted business state.
- No implementation in this increment may permit more than three future agent tasks to run concurrently.
- The existing static LaunchLoop demo and its six evaluation cases must continue to work.
- Use synthetic data only.
- Follow the official integration patterns in:
  - https://docs.djangoproject.com/en/5.2/intro/tutorial01/
  - https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html
  - https://vite.dev/guide/
  - https://docs.astral.sh/uv/guides/integration/docker/

## File Structure

The increment creates these boundaries:

```text
.
├── backend/
│   ├── manage.py                         # Django command entry point
│   ├── civicloop/
│   │   ├── __init__.py                   # Loads the Celery application
│   │   ├── asgi.py                       # ASGI entry point
│   │   ├── celery.py                     # Celery application configuration
│   │   ├── settings.py                   # Environment-driven Django/Celery settings
│   │   ├── urls.py                       # API and SPA routes
│   │   └── wsgi.py                       # WSGI entry point
│   ├── foundation/
│   │   ├── apps.py                       # Foundation Django app
│   │   └── tasks.py                      # Worker smoke task only
│   └── health/
│       ├── apps.py                       # Health Django app
│       ├── checks.py                     # Dependency readiness checks
│       ├── urls.py                       # Versioned health routes
│       └── views.py                      # Liveness/readiness responses
├── frontend/
│   ├── src/
│   │   ├── App.test.tsx                  # Application-shell component tests
│   │   ├── App.tsx                       # CivicLoop shell
│   │   ├── index.css                     # Accessible foundation styling
│   │   └── main.tsx                      # React entry point
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── tests/
│   ├── foundation/test_celery.py         # Celery configuration/task tests
│   ├── health/test_health_api.py          # Health-contract tests
│   └── test_settings.py                   # Configuration tests
├── docker/entrypoint.sh                  # Image process-mode dispatcher
├── scripts/readiness.py                  # Operator-facing readiness command
├── .dockerignore
├── .env.example
├── compose.yaml
├── compose.dev.yaml
├── Dockerfile
├── pyproject.toml
├── uv.lock
└── .github/workflows/ci.yml
```

The plan does not move or rewrite `loops/launchloop`. Its evaluator remains an independent regression gate.

---

### Task 1: Python and Django Project Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `backend/manage.py`
- Create: `backend/civicloop/__init__.py`
- Create: `backend/civicloop/settings.py`
- Create: `backend/civicloop/urls.py`
- Create: `backend/civicloop/asgi.py`
- Create: `backend/civicloop/wsgi.py`
- Create: `backend/foundation/__init__.py`
- Create: `backend/foundation/apps.py`
- Create: `tests/test_settings.py`
- Create: `tests/__init__.py`
- Create: `uv.lock` using `uv lock`

**Interfaces:**
- Consumes: Environment variables documented in `.env.example` in Task 5.
- Produces: Django settings module `civicloop.settings`, URL configuration `civicloop.urls`, and importable application package `civicloop`.

- [ ] **Step 1: Add the dependency and test configuration**

Create `pyproject.toml`:

```toml
[project]
name = "civicloop"
version = "0.1.0"
description = "Open-source agentic workflow loops for nonprofit operations"
requires-python = ">=3.11,<3.12"
dependencies = [
  "celery[redis]>=5.6,<5.7",
  "dj-database-url>=3,<4",
  "django>=5.2,<5.3",
  "gunicorn>=23,<24",
  "psycopg[binary]>=3.2,<4",
  "whitenoise>=6.9,<7",
]

[dependency-groups]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-django>=4.11,<5",
  "ruff>=0.12,<1",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "civicloop.settings"
pythonpath = ["backend"]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "DJ"]

[tool.mypy]
python_version = "3.11"
check_untyped_defs = true
disallow_untyped_defs = true
no_implicit_optional = true
warn_return_any = true
warn_unused_ignores = true
```

Create `tests/test_settings.py`:

```python
from django.conf import settings


def test_test_environment_uses_in_memory_database() -> None:
    assert settings.ENVIRONMENT == "test"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.DATABASES["default"]["NAME"] == ":memory:"


def test_default_agent_concurrency_never_exceeds_three() -> None:
    assert 1 <= settings.AGENT_MAX_CONCURRENCY <= 3
```

- [ ] **Step 2: Generate the lockfile**

Run from the repository root:

```powershell
docker run --rm `
  -v "${PWD}:/app" `
  -v civicloop-uv-cache:/root/.cache/uv `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv lock --python 3.11
```

Expected: `uv.lock` is created and contains Django 5.2.x and Celery 5.6.x.

- [ ] **Step 3: Run the test to verify the project is missing**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -v civicloop-uv-cache:/root/.cache/uv `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/test_settings.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'civicloop'`.

- [ ] **Step 4: Add the minimal Django project**

Create `backend/manage.py`:

```python
#!/usr/bin/env python
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "civicloop.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

Create `backend/civicloop/__init__.py` as an empty file.

Create `backend/civicloop/settings.py`:

```python
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent

ENVIRONMENT = os.getenv("CIVICLOOP_ENV", "development")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-not-for-production")
DEBUG = ENVIRONMENT == "development"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "foundation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "civicloop.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "civicloop.wsgi.application"
ASGI_APPLICATION = "civicloop.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default="postgresql://civicloop:civicloop@localhost:5432/civicloop",
        conn_max_age=60,
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/assets/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AGENT_MAX_CONCURRENCY = min(max(int(os.getenv("AGENT_MAX_CONCURRENCY", "3")), 1), 3)
```

Create `backend/civicloop/urls.py`:

```python
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
```

Create both `backend/civicloop/asgi.py` and `backend/civicloop/wsgi.py`, changing only `get_asgi_application` to `get_wsgi_application` and `application` type as appropriate:

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "civicloop.settings")
application = get_asgi_application()
```

```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "civicloop.settings")
application = get_wsgi_application()
```

Create `backend/foundation/__init__.py` as an empty file.

Create `backend/foundation/apps.py`:

```python
from django.apps import AppConfig


class FoundationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "foundation"
```

- [ ] **Step 5: Run the backend checks**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -v civicloop-uv-cache:/root/.cache/uv `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/test_settings.py -v
```

Expected: 2 tests PASS.

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 python backend/manage.py check
```

Expected: `System check identified no issues`.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml uv.lock backend tests/test_settings.py
git commit -m "build: scaffold Django application foundation"
```

---

### Task 2: Versioned Health and Readiness API

**Files:**
- Create: `backend/health/__init__.py`
- Create: `backend/health/apps.py`
- Create: `backend/health/checks.py`
- Create: `backend/health/urls.py`
- Create: `backend/health/views.py`
- Create: `tests/health/__init__.py`
- Create: `tests/health/test_health_api.py`
- Modify: `backend/civicloop/settings.py`
- Modify: `backend/civicloop/urls.py`

**Interfaces:**
- Consumes: Django settings and configured `default` database/cache aliases.
- Produces: `GET /api/v1/health/live` and `GET /api/v1/health/ready`, plus `DependencyStatus` and `readiness_status()`.

- [ ] **Step 1: Write failing health-contract tests**

Create `tests/health/__init__.py` as an empty file.

Create `tests/health/test_health_api.py`:

```python
from unittest.mock import patch

from django.test import Client

from health.checks import DependencyStatus


def test_liveness_does_not_call_dependencies() -> None:
    response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("health.views.readiness_status")
def test_readiness_returns_200_when_dependencies_are_ready(mock_status) -> None:
    mock_status.return_value = [
        DependencyStatus(name="postgres", ready=True),
        DependencyStatus(name="valkey", ready=True),
    ]

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["dependencies"] == {
        "postgres": {"ready": True},
        "valkey": {"ready": True},
    }


@patch("health.views.readiness_status")
def test_readiness_returns_503_without_leaking_exception_text(mock_status) -> None:
    mock_status.return_value = [
        DependencyStatus(name="postgres", ready=False),
        DependencyStatus(name="valkey", ready=True),
    ]

    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {
            "postgres": {"ready": False},
            "valkey": {"ready": True},
        },
    }
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/health/test_health_api.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'health'`.

- [ ] **Step 3: Implement dependency checks and views**

Create `backend/health/__init__.py` as an empty file.

Create `backend/health/apps.py`:

```python
from django.apps import AppConfig


class HealthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "health"
```

Create `backend/health/checks.py`:

```python
from dataclasses import dataclass

from django.core.cache import caches
from django.db import connections


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ready: bool


def postgres_is_ready() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:
        return False


def valkey_is_ready() -> bool:
    try:
        cache = caches["default"]
        cache.set("civicloop:readiness", "ok", timeout=5)
        return cache.get("civicloop:readiness") == "ok"
    except Exception:
        return False


def readiness_status() -> list[DependencyStatus]:
    return [
        DependencyStatus(name="postgres", ready=postgres_is_ready()),
        DependencyStatus(name="valkey", ready=valkey_is_ready()),
    ]
```

Create `backend/health/views.py`:

```python
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .checks import readiness_status


@require_GET
def live(_request) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def ready(_request) -> JsonResponse:
    statuses = readiness_status()
    dependencies = {
        status.name: {"ready": status.ready}
        for status in statuses
    }
    all_ready = all(status.ready for status in statuses)
    return JsonResponse(
        {
            "status": "ready" if all_ready else "not_ready",
            "dependencies": dependencies,
        },
        status=200 if all_ready else 503,
    )
```

Create `backend/health/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("live", views.live, name="health-live"),
    path("ready", views.ready, name="health-ready"),
]
```

Add `"health",` after `"foundation",` in `INSTALLED_APPS`.

Add the cache configuration to `backend/civicloop/settings.py`:

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("VALKEY_URL", "redis://localhost:6379/0"),
    }
}
```

Replace `backend/civicloop/urls.py` with:

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", include("health.urls")),
]
```

- [ ] **Step 4: Run health and full backend tests**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -e VALKEY_URL=redis://invalid:6379/0 `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/health/test_health_api.py tests/test_settings.py -v
```

Expected: 5 tests PASS. The readiness dependency calls are mocked, so no real Valkey is required by these unit tests.

- [ ] **Step 5: Commit**

```powershell
git add backend/health backend/civicloop/settings.py backend/civicloop/urls.py tests/health
git commit -m "feat: add liveness and readiness contracts"
```

---

### Task 3: Celery Process Foundation

**Files:**
- Create: `backend/civicloop/celery.py`
- Create: `backend/foundation/tasks.py`
- Create: `tests/foundation/__init__.py`
- Create: `tests/foundation/test_celery.py`
- Modify: `backend/civicloop/__init__.py`
- Modify: `backend/civicloop/settings.py`

**Interfaces:**
- Consumes: `VALKEY_URL` and Django settings.
- Produces: Celery app `civicloop.celery.app` and smoke task `foundation.tasks.ping() -> str`.

- [ ] **Step 1: Write the failing Celery test**

Create `tests/foundation/__init__.py` as an empty file.

Create `tests/foundation/test_celery.py`:

```python
from civicloop.celery import app
from foundation.tasks import ping


def test_celery_uses_django_configuration_namespace() -> None:
    assert app.main == "civicloop"
    assert app.conf.broker_url == "redis://valkey:6379/1"


def test_ping_task_returns_pong(settings) -> None:
    settings.CELERY_TASK_ALWAYS_EAGER = True
    assert ping.apply().get() == "pong"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -e VALKEY_URL=redis://valkey:6379/0 `
  -e CELERY_BROKER_URL=redis://valkey:6379/1 `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/foundation/test_celery.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'civicloop.celery'`.

- [ ] **Step 3: Implement the documented Django/Celery integration**

Create `backend/civicloop/celery.py`:

```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "civicloop.settings")

app = Celery("civicloop")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

Replace `backend/civicloop/__init__.py` with:

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

Create `backend/foundation/tasks.py`:

```python
from celery import shared_task


@shared_task
def ping() -> str:
    return "pong"
```

Add to `backend/civicloop/settings.py`:

```python
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270
CELERY_WORKER_CONCURRENCY = min(
    max(int(os.getenv("CELERY_WORKER_CONCURRENCY", "1")), 1),
    AGENT_MAX_CONCURRENCY,
)
```

Change the smoke task to opt into a result despite the application-wide default:

```python
@shared_task(ignore_result=False)
def ping() -> str:
    return "pong"
```

- [ ] **Step 4: Run the Celery and backend tests**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -e VALKEY_URL=redis://valkey:6379/0 `
  -e CELERY_BROKER_URL=redis://valkey:6379/1 `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests -v
```

Expected: all backend tests PASS.

- [ ] **Step 5: Run static checks**

Run:

```powershell
docker run --rm `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 ruff check backend tests
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```powershell
git add backend/civicloop backend/foundation/tasks.py tests/foundation
git commit -m "feat: add Celery worker foundation"
```

---

### Task 4: React Application Shell

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/package-lock.json` using `npm install --package-lock-only`

**Interfaces:**
- Consumes: `GET /api/v1/health/live`.
- Produces: compiled SPA in `frontend/dist`, with an accessible shell and `App` component.

- [ ] **Step 1: Add frontend dependency and test configuration**

Create `frontend/package.json`:

```json
{
  "name": "@civicloop/web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "tsc -b && vite build",
    "dev": "vite --host 0.0.0.0",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "19.2.8",
    "react-dom": "19.2.8"
  },
  "devDependencies": {
    "@testing-library/dom": "10.4.1",
    "@testing-library/jest-dom": "6.8.0",
    "@testing-library/react": "16.3.2",
    "@testing-library/user-event": "14.6.1",
    "@types/react": "19.2.17",
    "@types/react-dom": "19.2.3",
    "@vitejs/plugin-react": "6.0.4",
    "jsdom": "30.0.0",
    "typescript": "7.0.2",
    "vite": "8.1.5",
    "vitest": "4.1.10"
  }
}
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src", "vite.config.ts"]
}
```

Create `frontend/vite.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://web:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
```

Create `frontend/src/test/setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 2: Write the failing application-shell test**

Create `frontend/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("shows the CivicLoop foundation and three future agent lanes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok" }),
    }),
  );

  render(<App />);

  expect(screen.getByRole("heading", { name: "CivicLoop" })).toBeInTheDocument();
  expect(screen.getByText("Event Readiness")).toBeInTheDocument();
  expect(screen.getByText("Campaign Composer")).toBeInTheDocument();
  expect(screen.getByText("Audience and Policy")).toBeInTheDocument();
  expect(await screen.findByText("Application healthy")).toBeInTheDocument();
});
```

- [ ] **Step 3: Install exact dependencies and verify the test fails**

Run:

```powershell
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm install
```

Run:

```powershell
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm test
```

Expected: FAIL with `Cannot find module './App'`.

- [ ] **Step 4: Implement the accessible shell**

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#123f37" />
    <title>CivicLoop</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Create `frontend/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";

const lanes = ["Event Readiness", "Campaign Composer", "Audience and Policy"];

export function App() {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/v1/health/live")
      .then((response) => {
        if (!response.ok) throw new Error("Health request failed");
        return response.json();
      })
      .then(() => setHealthy(true))
      .catch(() => setHealthy(false));
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Open-source nonprofit operations</p>
          <h1>CivicLoop</h1>
        </div>
        <span className={`health ${healthy === false ? "health--down" : ""}`}>
          {healthy === null
            ? "Checking application"
            : healthy
              ? "Application healthy"
              : "Application unavailable"}
        </span>
      </header>
      <main>
        <section className="intro" aria-labelledby="foundation-title">
          <div>
            <p className="eyebrow">Foundation increment</p>
            <h2 id="foundation-title">Human-approved agent workflows</h2>
            <p>
              The platform shell is running. Identity, event input, live agents,
              and approvals arrive in independently reviewed increments.
            </p>
          </div>
          <button type="button" disabled title="Available in a later increment">
            Start LaunchLoop
          </button>
        </section>
        <section aria-labelledby="agents-title">
          <h2 id="agents-title">Agent workspace</h2>
          <div className="lanes">
            {lanes.map((lane) => (
              <article className="lane" key={lane}>
                <span aria-hidden="true" className="lane__status" />
                <h3>{lane}</h3>
                <p>Not configured</p>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
```

Create `frontend/src/index.css`:

```css
:root {
  color: #17211f;
  background: #f4f7f6;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  font-synthesis: none;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
}

button,
input,
textarea,
select {
  font: inherit;
}

button:focus-visible,
a:focus-visible {
  outline: 3px solid #f2b84b;
  outline-offset: 3px;
}

.app-shell {
  min-height: 100vh;
}

.topbar {
  align-items: center;
  background: #123f37;
  color: white;
  display: flex;
  justify-content: space-between;
  padding: 1rem clamp(1rem, 4vw, 3rem);
}

.topbar h1,
.intro h2 {
  margin: 0;
}

.eyebrow {
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  margin: 0 0 0.25rem;
  text-transform: uppercase;
}

.health {
  background: #dff4ed;
  border-radius: 999px;
  color: #165e50;
  font-size: 0.85rem;
  font-weight: 700;
  padding: 0.45rem 0.75rem;
}

.health--down {
  background: #fde7e4;
  color: #8b2d24;
}

main {
  margin: 0 auto;
  max-width: 1200px;
  padding: clamp(1rem, 4vw, 3rem);
}

.intro {
  align-items: end;
  background: white;
  border: 1px solid #d9e1df;
  border-radius: 0.75rem;
  display: flex;
  gap: 2rem;
  justify-content: space-between;
  padding: clamp(1rem, 3vw, 2rem);
}

.intro p {
  max-width: 65ch;
}

button {
  background: #dce3e1;
  border: 0;
  border-radius: 0.4rem;
  color: #596562;
  padding: 0.75rem 1rem;
}

.lanes {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.lane {
  background: white;
  border: 1px solid #d9e1df;
  border-radius: 0.75rem;
  min-height: 9rem;
  padding: 1.25rem;
}

.lane__status {
  background: #8c9895;
  border-radius: 50%;
  display: inline-block;
  height: 0.6rem;
  width: 0.6rem;
}

@media (max-width: 760px) {
  .intro {
    align-items: stretch;
    flex-direction: column;
  }

  .lanes {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 5: Run frontend tests and build**

Run:

```powershell
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm test
```

Expected: 1 test PASS.

Run:

```powershell
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm run build
```

Expected: TypeScript and Vite build successfully and create `frontend/dist/index.html`.

- [ ] **Step 6: Commit**

```powershell
git add frontend
git commit -m "feat: add CivicLoop React application shell"
```

---

### Task 5: Single Image and Docker Compose Runtime

**Files:**
- Create: `Dockerfile`
- Create: `docker/entrypoint.sh`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `compose.yaml`
- Create: `compose.dev.yaml`
- Modify: `backend/civicloop/settings.py`
- Modify: `backend/civicloop/urls.py`
- Create: `tests/test_spa.py`

**Interfaces:**
- Consumes: `frontend/dist`, `uv.lock`, process mode as the first container argument, and documented environment variables.
- Produces: one `civicloop` image with `web`, `worker`, `scheduler`, and `manage` modes; Compose services `db`, `valkey`, `migrate`, `web`, `worker`, and `scheduler`.

- [ ] **Step 1: Write the failing SPA route test**

Create `tests/test_spa.py`:

```python
from pathlib import Path

from django.test import Client, override_settings


def test_spa_route_serves_compiled_index(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>CivicLoop</title>", encoding="utf-8")

    with override_settings(FRONTEND_INDEX=index):
        response = Client().get("/")

    assert response.status_code == 200
    assert b"<title>CivicLoop</title>" in response.content


def test_unknown_api_route_does_not_fall_back_to_spa() -> None:
    response = Client().get("/api/v1/does-not-exist")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/test_spa.py -v
```

Expected: FAIL because `/` returns 404.

- [ ] **Step 3: Add the SPA fallback without masking API 404s**

Add to `backend/civicloop/settings.py`:

```python
FRONTEND_DIST = REPOSITORY_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

if (FRONTEND_DIST / "assets").exists():
    STATICFILES_DIRS = [FRONTEND_DIST / "assets"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
```

Replace `backend/civicloop/urls.py` with:

```python
from django.conf import settings
from django.contrib import admin
from django.http import FileResponse
from django.urls import include, path
from django.views.decorators.http import require_GET


@require_GET
def spa_index(_request) -> FileResponse:
    return FileResponse(open(settings.FRONTEND_INDEX, "rb"), content_type="text/html")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", include("health.urls")),
    path("", spa_index, name="spa-index"),
]
```

- [ ] **Step 4: Add the process dispatcher**

Create `docker/entrypoint.sh`:

```sh
#!/bin/sh
set -eu

mode="${1:-web}"

case "$mode" in
  web)
    exec gunicorn civicloop.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-2}" \
      --access-logfile -
    ;;
  worker)
    exec celery -A civicloop worker \
      --loglevel "${LOG_LEVEL:-INFO}" \
      --concurrency "${CELERY_WORKER_CONCURRENCY:-1}"
    ;;
  scheduler)
    exec celery -A civicloop beat \
      --loglevel "${LOG_LEVEL:-INFO}" \
      --schedule /tmp/celerybeat-schedule
    ;;
  manage)
    shift
    exec python backend/manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
```

- [ ] **Step 5: Add the multi-stage production image**

Create `Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:22.23.0-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11.13-slim-bookworm AS python-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM python:3.11.13-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 civicloop \
    && useradd --system --uid 10001 --gid civicloop --home /app civicloop
WORKDIR /app
COPY --from=python-builder /app/.venv /app/.venv
COPY backend/ /app/backend/
COPY --from=frontend /build/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod 0555 /app/docker/entrypoint.sh \
    && mkdir -p /app/backend/staticfiles \
    && chown -R civicloop:civicloop /app
USER civicloop
RUN python backend/manage.py collectstatic --noinput
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
```

Create `.dockerignore`:

```text
.git
.github
.superpowers
.venv
**/__pycache__
**/*.pyc
.env
node_modules
frontend/node_modules
frontend/dist
loops
docs
```

- [ ] **Step 6: Add environment and Compose contracts**

Create `.env.example`:

```dotenv
CIVICLOOP_ENV=production
DJANGO_SECRET_KEY=replace-with-at-least-50-random-characters
DJANGO_ALLOWED_HOSTS=localhost
DATABASE_URL=postgresql://civicloop:change-me@db:5432/civicloop
POSTGRES_DB=civicloop
POSTGRES_USER=civicloop
POSTGRES_PASSWORD=change-me
VALKEY_URL=redis://valkey:6379/0
CELERY_BROKER_URL=redis://valkey:6379/1
AGENT_MAX_CONCURRENCY=3
CELERY_WORKER_CONCURRENCY=1
WEB_CONCURRENCY=2
PORT=8000
```

Create `compose.yaml`:

```yaml
name: civicloop

x-app: &app
  build:
    context: .
  image: civicloop:local
  env_file:
    - .env
  restart: unless-stopped

services:
  db:
    image: postgres:17.5-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20
    volumes:
      - postgres-data:/var/lib/postgresql/data

  valkey:
    image: valkey/valkey:8.1-alpine
    command: ["valkey-server", "--save", "", "--appendonly", "no"]
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20

  migrate:
    <<: *app
    command: ["manage", "migrate", "--noinput"]
    restart: "no"
    depends_on:
      db:
        condition: service_healthy

  web:
    <<: *app
    command: ["web"]
    ports:
      - "8000:8000"
    depends_on:
      migrate:
        condition: service_completed_successfully
      valkey:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/live')",
        ]
      interval: 10s
      timeout: 3s
      retries: 12

  worker:
    <<: *app
    command: ["worker"]
    depends_on:
      migrate:
        condition: service_completed_successfully
      valkey:
        condition: service_healthy

  scheduler:
    <<: *app
    command: ["scheduler"]
    depends_on:
      migrate:
        condition: service_completed_successfully
      valkey:
        condition: service_healthy
    tmpfs:
      - /tmp

volumes:
  postgres-data:
```

Create `compose.dev.yaml`:

```yaml
services:
  web:
    environment:
      CIVICLOOP_ENV: development
    volumes:
      - ./backend:/app/backend

  frontend:
    image: node:22.23.0-bookworm-slim
    working_dir: /app
    command: ["sh", "-c", "npm ci && npm run dev"]
    volumes:
      - ./frontend:/app
      - frontend-node-modules:/app/node_modules
    ports:
      - "5173:5173"
    depends_on:
      web:
        condition: service_healthy

volumes:
  frontend-node-modules:
```

- [ ] **Step 7: Run unit tests, build, and Compose smoke tests**

Copy the example configuration:

```powershell
Copy-Item .env.example .env
```

Replace both `change-me` values and `DJANGO_SECRET_KEY` in the untracked `.env` with local random values.

Run:

```powershell
docker compose build
```

Expected: one `civicloop:local` image builds successfully and includes `frontend/dist/index.html`.

Run:

```powershell
docker compose up -d
docker compose ps
```

Expected: `db`, `valkey`, `web`, `worker`, and `scheduler` are running; `migrate` exited with code 0; `web` becomes healthy.

Run:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready
```

Expected:

```text
status
------
ok

status dependencies
------ ------------
ready ...
```

Run:

```powershell
docker compose exec web python backend/manage.py check --deploy
docker compose exec worker celery -A civicloop inspect ping
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests -v
python .\loops\launchloop\launchloop.py |
  Select-String -Pattern '"passed"|"total"' |
  Select-Object -First 2
```

Expected:

- Django reports no deployment errors; development-only warnings are documented for the later Caddy/security increment.
- All backend tests pass.
- The worker responds with `pong`.
- LaunchLoop still reports `"passed": 6` and `"total": 6`.

- [ ] **Step 8: Stop the smoke environment and commit**

Run:

```powershell
docker compose down
```

Do not add `-v`; the local database volume is intentionally preserved.

Commit:

```powershell
git add Dockerfile docker .dockerignore .env.example compose.yaml compose.dev.yaml backend/civicloop tests/test_spa.py
git commit -m "build: add single-image Compose runtime"
```

---

### Task 6: Readiness Command, CI, and Operator Documentation

**Files:**
- Create: `scripts/readiness.py`
- Create: `tests/scripts/test_readiness.py`
- Create: `tests/scripts/__init__.py`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `/api/v1/health/live` and `/api/v1/health/ready`.
- Produces: `python scripts/readiness.py --base-url URL -> exit code 0|1` and required CI checks.

- [ ] **Step 1: Write the failing readiness-command tests**

Create `tests/scripts/__init__.py` as an empty file.

Create `tests/scripts/test_readiness.py`:

```python
import json
from unittest.mock import patch

from scripts.readiness import main


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


@patch("scripts.readiness.urlopen")
def test_readiness_returns_zero_when_both_endpoints_pass(mock_open) -> None:
    mock_open.side_effect = [
        FakeResponse({"status": "ok"}),
        FakeResponse({"status": "ready", "dependencies": {}}),
    ]

    assert main(["--base-url", "http://civicloop.test"]) == 0


@patch("scripts.readiness.urlopen", side_effect=OSError("connection refused"))
def test_readiness_returns_one_without_printing_secrets(_mock_open, capsys) -> None:
    assert main(["--base-url", "http://civicloop.test"]) == 1
    assert "connection refused" not in capsys.readouterr().out
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/scripts/test_readiness.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.readiness'`.

- [ ] **Step 3: Implement the dependency-free readiness command**

Create `scripts/readiness.py`:

```python
import argparse
import json
import sys
from urllib.request import urlopen


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check CivicLoop readiness")
    parser.add_argument("--base-url", default="http://localhost:8000")
    return parser.parse_args(argv)


def fetch_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or [])
    try:
        live = fetch_json(f"{args.base_url}/api/v1/health/live")
        ready = fetch_json(f"{args.base_url}/api/v1/health/ready")
    except Exception:
        print("CivicLoop is not reachable or not ready.")
        return 1

    if live.get("status") != "ok" or ready.get("status") != "ready":
        print("CivicLoop is reachable but not ready.")
        return 1

    print("CivicLoop is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the readiness tests**

Run:

```powershell
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests/scripts/test_readiness.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Add required continuous-integration gates**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17.5-alpine
        env:
          POSTGRES_DB: civicloop
          POSTGRES_USER: civicloop
          POSTGRES_PASSWORD: civicloop
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U civicloop -d civicloop"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
      valkey:
        image: valkey/valkey:8.1-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "valkey-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
    env:
      CIVICLOOP_ENV: test
      DJANGO_SECRET_KEY: ci-only-secret
      DATABASE_URL: postgresql://civicloop:civicloop@localhost:5432/civicloop
      VALKEY_URL: redis://localhost:6379/0
      CELERY_BROKER_URL: redis://localhost:6379/1
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.11.32"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run ruff check backend tests scripts
      - run: uv run python backend/manage.py migrate --noinput
      - run: uv run pytest -v
      - run: python loops/launchloop/launchloop.py > launchloop-results.json
      - run: |
          python -c "import json; data=json.load(open('launchloop-results.json')); assert data['summary'] == {'passed': 6, 'total': 6}"

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22.23.0"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm test
      - run: npm run build

  compose:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    steps:
      - uses: actions/checkout@v4
      - run: cp .env.example .env
      - run: |
          sed -i 's/replace-with-at-least-50-random-characters/ci-only-secret-that-is-long-enough-for-foundation-smoke/' .env
          sed -i 's/change-me/civicloop-ci/g' .env
      - run: docker compose build
      - run: docker compose up -d
      - run: |
          for attempt in {1..30}; do
            python scripts/readiness.py && exit 0
            sleep 2
          done
          docker compose ps
          docker compose logs
          exit 1
      - if: always()
        run: docker compose down -v
```

- [ ] **Step 6: Replace the README with foundation-aware setup instructions**

Keep the existing CivicLoop and LaunchLoop descriptions, and add this section after the repository overview:

```markdown
## Application Foundation

CivicLoop now includes the container foundation for the self-hosted application.
The current increment provides the web shell, health contracts, PostgreSQL,
Valkey, and Celery process modes. Authentication and live LaunchLoop agents are
delivered in later reviewed increments.

### Prerequisites

- Docker 29 or newer
- Docker Compose 5 or newer

No host Python, Node.js, PostgreSQL client, or Valkey installation is required.

### Start

```powershell
Copy-Item .env.example .env
# Replace the example passwords and secret in the untracked .env file.
docker compose up -d --build
python .\scripts\readiness.py
```

Open http://localhost:8000.

### Verify

```powershell
docker compose exec web python backend/manage.py check
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests -v
python .\loops\launchloop\launchloop.py
```

Expected LaunchLoop result: `6 / 6` eval cases pass.

### Stop

```powershell
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the
local PostgreSQL volume and all of its data.
```

- [ ] **Step 7: Run every foundation gate locally**

Run:

```powershell
docker compose build
docker compose up -d
python .\scripts\readiness.py
docker compose exec web python backend/manage.py check
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests -v
docker run --rm `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 ruff check backend tests scripts
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm test
docker run --rm `
  -v "${PWD}/frontend:/app" `
  -w /app `
  node:22.23.0-bookworm-slim `
  npm run build
python .\loops\launchloop\launchloop.py |
  Select-String -Pattern '"passed"|"total"' |
  Select-Object -First 2
```

Expected:

- readiness prints `CivicLoop is ready.`
- all backend and frontend tests pass,
- Ruff reports no violations,
- Django reports no system-check errors,
- frontend build succeeds,
- LaunchLoop reports 6 passed of 6 total.

- [ ] **Step 8: Confirm repository hygiene and commit**

Run:

```powershell
git status --short
git diff --check
git ls-files .env
```

Expected:

- `.env` is not listed by `git ls-files`,
- generated `frontend/dist`, `.venv`, caches, and local volumes are not staged,
- only intentional source, lockfile, CI, and documentation changes remain.

Commit:

```powershell
git add .github/workflows/ci.yml README.md scripts tests/scripts
git commit -m "ci: verify CivicLoop foundation"
```

---

## Plan Completion Gate

Before declaring this increment complete:

- [ ] `git status --short` is clean.
- [ ] All backend tests pass against PostgreSQL and Valkey in CI.
- [ ] Frontend tests and production build pass.
- [ ] The single image starts successfully in web, worker, and scheduler modes.
- [ ] Compose readiness passes from a clean installation.
- [ ] The existing LaunchLoop evaluator remains 6/6.
- [ ] No secret, `.env`, generated frontend output, or dependency directory is tracked.
- [ ] The implementation stays within foundation scope; it does not add identity, Event models, approvals, Hermes execution, or Kubernetes resources.

The next separately reviewed implementation plan is **Identity, Invitations, Roles, MFA, and Audit**.
