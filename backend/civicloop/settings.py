import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent

ENVIRONMENT = os.getenv("CIVICLOOP_ENV", "development")


def _environment_boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean.")


CIVICLOOP_ADMIN_IDENTITY_ENABLED = _environment_boolean(
    "CIVICLOOP_ADMIN_IDENTITY_ENABLED",
    False,
)
configured_identity_key_file = os.getenv("CIVICLOOP_IDENTITY_KEY_FILE", "").strip()
CIVICLOOP_IDENTITY_KEY_FILE = (
    Path(configured_identity_key_file) if configured_identity_key_file else None
)
if CIVICLOOP_ADMIN_IDENTITY_ENABLED:
    identity_key_file_is_safe = (
        CIVICLOOP_IDENTITY_KEY_FILE is not None
        and CIVICLOOP_IDENTITY_KEY_FILE.is_file()
        and os.access(CIVICLOOP_IDENTITY_KEY_FILE, os.R_OK)
    )
    if identity_key_file_is_safe and os.name != "nt":
        identity_key_file_is_safe = not bool(
            CIVICLOOP_IDENTITY_KEY_FILE.stat().st_mode & 0o077
        )
    if not identity_key_file_is_safe:
        raise ImproperlyConfigured(
            "The administrator identity key file must be configured as a readable "
            "regular file with owner-only permissions."
        )

ADMIN_PREAUTH_SECONDS = 5 * 60
ADMIN_IDLE_SECONDS = 30 * 60
ADMIN_ABSOLUTE_SECONDS = 12 * 60 * 60
ADMIN_FRESH_SECONDS = 10 * 60
ADMIN_TRUSTED_PROXY_IPS = frozenset(
    value.strip()
    for value in os.getenv("CIVICLOOP_ADMIN_TRUSTED_PROXY_IPS", "").split(",")
    if value.strip()
)
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

DEVELOPMENT_DEMO_PASSWORD = "civicloop-demo"
DOCUMENTED_PLACEHOLDER_DEMO_PASSWORD = "replace-with-a-unique-demo-password"
DEMO_PASSWORD = os.getenv("CIVICLOOP_DEMO_PASSWORD", DEVELOPMENT_DEMO_PASSWORD)
if ENVIRONMENT not in {"development", "test"} and (
    not DEMO_PASSWORD
    or DEMO_PASSWORD
    in {DEVELOPMENT_DEMO_PASSWORD, DOCUMENTED_PLACEHOLDER_DEMO_PASSWORD}
):
    raise ImproperlyConfigured(
        "CIVICLOOP_DEMO_PASSWORD must be set to a non-default value outside "
        "development and test."
    )
DEBUG = ENVIRONMENT == "development"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

if ENVIRONMENT == "production":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "api_contracts",
    "foundation",
    "health",
    "identity",
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
SWAGGER_INDEX = FRONTEND_DIST / "swagger.html"
API_CONTRACT_ROOTS = {
    "openapi": REPOSITORY_ROOT / "openapi",
    "schemas": REPOSITORY_ROOT / "schemas",
}

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

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

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
