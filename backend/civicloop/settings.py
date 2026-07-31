import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent

ENVIRONMENT = os.getenv("CIVICLOOP_ENV", "development")
DEVELOPMENT_SECRET_KEY = "development-only-not-for-production"
DOCUMENTED_PLACEHOLDER_SECRET_KEY = "replace-with-at-least-50-random-characters"
INSECURE_PRODUCTION_SECRET_KEYS = {
    DEVELOPMENT_SECRET_KEY,
    DOCUMENTED_PLACEHOLDER_SECRET_KEY,
}
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEVELOPMENT_SECRET_KEY)
if ENVIRONMENT not in {"development", "test"} and (
    not SECRET_KEY or SECRET_KEY in INSECURE_PRODUCTION_SECRET_KEYS
):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a non-default value outside development and test."
    )
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
    "health",
    "launchloop",
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

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("VALKEY_URL", "redis://localhost:6379/0"),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/assets/"
STATIC_ROOT = BASE_DIR / "staticfiles"
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

try:
    configured_agent_concurrency = int(os.getenv("AGENT_MAX_CONCURRENCY", "3"))
except (TypeError, ValueError):
    raise ImproperlyConfigured("AGENT_MAX_CONCURRENCY must be an integer.") from None

AGENT_MAX_CONCURRENCY = min(max(configured_agent_concurrency, 1), 3)

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270

try:
    configured_celery_worker_concurrency = int(os.getenv("CELERY_WORKER_CONCURRENCY", "1"))
except (TypeError, ValueError):
    raise ImproperlyConfigured("CELERY_WORKER_CONCURRENCY must be an integer.") from None

CELERY_WORKER_CONCURRENCY = min(
    max(configured_celery_worker_concurrency, 1), AGENT_MAX_CONCURRENCY
)
