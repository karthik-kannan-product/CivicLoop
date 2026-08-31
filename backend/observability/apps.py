from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "observability"

    def ready(self) -> None:
        from .runtime import configure_from_django_settings

        configure_from_django_settings()

