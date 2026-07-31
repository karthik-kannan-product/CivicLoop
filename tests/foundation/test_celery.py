from civicloop.celery import app
from foundation.tasks import ping


def test_celery_uses_django_configuration_namespace() -> None:
    assert app.main == "civicloop"
    assert app.conf.broker_url == "redis://valkey:6379/1"


def test_ping_task_returns_pong(settings) -> None:
    settings.CELERY_TASK_ALWAYS_EAGER = True
    assert ping.apply().get() == "pong"
