import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent

ENVIRONMENT = os.getenv("CIVICLOOP_ENV", "development")
DEVELOPMENT_SECRET_KEY = "development-only-not-for-production"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEVELOPMENT_SECRET_KEY)
if ENVIRONMENT not in {"development", "test"} and (
    not SECRET_KEY or SECRET_KEY == DEVELOPMENT_SECRET_KEY
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

try:
    configured_agent_concurrency = int(os.getenv("AGENT_MAX_CONCURRENCY", "3"))
except (TypeError, ValueError):
    raise ImproperlyConfigured("AGENT_MAX_CONCURRENCY must be an integer.") from None

AGENT_MAX_CONCURRENCY = min(max(configured_agent_concurrency, 1), 3)
