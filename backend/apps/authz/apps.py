from django.apps import AppConfig


class AuthzConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.authz"
    label = "authz"
    verbose_name = "Authorization"

    def ready(self) -> None:
        from . import signals  # noqa: F401
