from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"

    def ready(self) -> None:
        # Registers the pre_save receiver that removes the file a product photo
        # replaced. `serializers` imports this module anyway, so the receiver
        # would usually be connected by the time a request arrives — but "usually"
        # is not a guarantee, and a management command that never touches the API
        # would leak a file on every re-upload.
        from . import images  # noqa: F401
