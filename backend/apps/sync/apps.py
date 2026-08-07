from django.apps import AppConfig


class SyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sync"
    label = "sync"

    def ready(self) -> None:
        # Importing registers the post_save/post_delete receivers that append to
        # the change log. Without this the mirror streams stay silent and every
        # Desktop quietly runs on a catalog that never updates.
        from . import receivers  # noqa: F401
