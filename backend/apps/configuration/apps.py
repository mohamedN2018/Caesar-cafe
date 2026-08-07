from django.apps import AppConfig


class ConfigurationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.configuration"
    label = "configuration"
    verbose_name = "Configuration"

    def ready(self) -> None:
        # Import for side effects: this is what populates the registry.
        from . import definitions  # noqa: F401
