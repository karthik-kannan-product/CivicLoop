from unittest.mock import patch

from civicloop.celery import app
from foundation.tasks import ping


def test_celery_uses_django_configuration_namespace() -> None:
    assert app.main == "civicloop"
    assert app.conf.broker_url == "redis://valkey:6379/1"


def test_ping_task_dispatches_eagerly_and_returns_a_result(settings) -> None:
    settings.CELERY_TASK_ALWAYS_EAGER = True

    assert app.conf.task_ignore_result is True
    assert ping.ignore_result is False

    with patch.object(
        app, "send_task", side_effect=AssertionError("broker dispatch is unexpected")
    ):
        result = ping.delay()

    assert result.get() == "pong"
