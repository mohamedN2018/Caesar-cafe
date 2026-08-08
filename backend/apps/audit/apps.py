from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"

    def ready(self) -> None:
        # Model-level receivers for the changes that are genuinely a row edit —
        # a price, a role assignment, a setting. Service-level actions (a void, a
        # refund, a count posting) call `services.record` directly, because only
        # the service knows the reason and who approved it.
        from . import receivers  # noqa: F401
