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
