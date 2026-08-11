"""
Product photographs, normalised on the way in.

A product photo is the only thing in this system that a member of staff uploads
from a phone, and a phone's camera roll is the worst input available: four
thousand pixels wide, several megabytes, rotated by a metadata tag that half the
world honours and half ignores, and carrying the GPS fix of wherever the picture
was taken.

None of that reaches the disk. What is stored is a bounded, re-encoded,
metadata-free copy, and the reason is not tidiness:

  * **The till loads the whole menu in one request.** Forty-three products at
    four megabytes each is a grid that never finishes drawing over the mobile
    connection C11 exists for, and a cashier who cannot see the grid cannot
    sell. This is the constraint that sets `MAX_EDGE`.
  * **A rotation tag is applied here or nowhere.** Re-encoding drops EXIF, so
    the orientation has to be baked into the pixels first. Skip it and every
    portrait photo taken on a phone arrives on its side.
  * **EXIF is deleted rather than carried.** The cafe's coordinates and the
    phone's model are in there, and `/media/` is served publicly by Caddy with
    a day of cache. Nothing in this product needs that metadata; everything
    about shipping it is a liability.

The client checks the size too. That is not a duplicate of this check and does
not replace it: a twelve-megabyte photo takes a visible while to upload before
the server can begin refusing it, and a manager doing the menu photo by photo
would sit through that every time. The client's copy is a courtesy; this one is
the rule.
"""

from __future__ import annotations

import logging
import secrets
from io import BytesIO
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models.signals import pre_save
from django.dispatch import receiver
from PIL import Image, ImageOps, UnidentifiedImageError, features

from apps.core.exceptions import AppError

from .models import Product

logger = logging.getLogger(__name__)

#: Refused outright. Paired with `MAX_IMAGE_BYTES` in
#: `frontend/src/views/catalog/ProductListView.vue` — the two are one number,
#: and a client limit above this one produces an upload that always fails after
#: the wait rather than before it.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

#: The longest edge of what gets stored, in pixels.
#:
#: Set from the largest consumer, which is the POS tile backdrop: on a wide
#: screen a tile is around 340 CSS pixels, so 680 covers it on a 2x display.
#: 900 leaves headroom for a use that wants the photo bigger without going back
#: and re-uploading the menu, and still lands each file near 100 KB.
MAX_EDGE = 900

#: High enough that the blur on the tile has real colour to work with, low
#: enough that the grid arrives. Above ~85 the file grows fast for detail that
#: a 7px blur at 28% opacity throws away anyway.
QUALITY = 82


def _target_format() -> tuple[str, str]:
    """
    WebP where it exists, JPEG where it does not.

    WebP is roughly a third smaller than JPEG at the same visible quality and
    keeps an alpha channel, so a cut-out with a transparent background survives
    instead of being flattened onto a guess at the background colour.

    The fallback is not defensive padding: WebP support depends on libwebp being
    present in whatever Pillow was installed, and the manylinux wheels carry it
    while a source build on a bare host may not. Discovering that as an
    exception on the first upload after a deploy — the one moment nobody is
    watching this code — is worse than storing a slightly larger JPEG.
    """
    if features.check("webp"):
        return "WEBP", "webp"
    return "JPEG", "jpg"


def _has_alpha(image: Image.Image) -> bool:
    # A palette image carries its transparency in `info`, not in a band, so
    # checking the mode alone reports a transparent GIF or PNG-8 as opaque and
    # then flattens it onto white.
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def assert_within_limit(uploaded: Any) -> None:
    """
    Refuse an oversized file **before** anything decodes it.

    The ordering is the whole reason this is a separate function.
    `ImageField.to_internal_value` runs Pillow's `verify()` on the way in, so a
    check placed in `validate_image` — which DRF runs afterwards — would report
    a 40MB upload as "not a valid image" if it happened to be malformed, and
    would have decoded it first if it was not. Neither is the answer the manager
    needs, and the second is the work this refusal exists to avoid.

    Raises `AppError` rather than a serializer `ValidationError`: the envelope
    puts a field-level error in `errors` behind a generic sentence in `message`,
    and the upload button shows `message`. Somebody whose photo was refused
    needs to be told it was too big, not that "the submitted data is incorrect".
    """
    size = getattr(uploaded, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise AppError(
            "الصورة أكبر من الحد المسموح (٤ م.ب.). برجاء اختيار صورة أصغر.",
            code="IMAGE_TOO_LARGE",
        )


def normalise(uploaded: Any) -> ContentFile:
    """Turn whatever was uploaded into the one shape this system stores."""
    # `verify()` has already run and, by its own documentation, leaves the file
    # unusable for reading pixels. Opening again from the start is required.
    uploaded.seek(0)
    try:
        image = Image.open(uploaded)
        # `open()` is lazy: a file truncated mid-transfer parses its header
        # fine and fails on the first pixel. `load()` moves that failure here,
        # where it becomes a 400, instead of into the resize below where it
        # would be a 500.
        image.load()
    # `DecompressionBombError` explicitly: a small file can decode to a
    # gigapixel image, which is the classic way to turn an upload form into a
    # memory exhaustion. Django's own ImageField catches it during `verify()` and
    # answers 400, so this is a second line rather than the only one — but the
    # first line lives in a framework and this is the code that allocates.
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise AppError(
            "تعذّر قراءة الصورة. برجاء اختيار صورة أخرى.",
            code="IMAGE_UNREADABLE",
        ) from exc

    # Bake the orientation in before the re-encode discards the tag that
    # describes it. `or image` because this returns None on some Pillow paths
    # and a silent None here would be a 500 on a valid photo.
    image = ImageOps.exif_transpose(image) or image

    image_format, extension = _target_format()
    if image_format == "JPEG" and _has_alpha(image):
        # JPEG has no alpha channel and `convert("RGB")` renders every
        # transparent pixel black — a cut-out arrives as a silhouette. White,
        # because that is what both surfaces this appears on are closest to.
        image = image.convert("RGBA")
        flattened = Image.new("RGB", image.size, (255, 255, 255))
        flattened.paste(image, mask=image.getchannel("A"))
        image = flattened
    else:
        image = image.convert("RGBA" if _has_alpha(image) else "RGB")

    # In place, aspect ratio kept, and it never enlarges — upscaling a small
    # photo spends bytes inventing detail that was never photographed.
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    options: dict[str, Any] = {"quality": QUALITY}
    if image_format == "WEBP":
        options["method"] = 6
    else:
        options["optimize"] = True
        options["progressive"] = True
    # No `exif=` and no `icc_profile=`: dropping both is the point, not an
    # oversight. Pillow carries neither forward unless asked to.
    image.save(buffer, format=image_format, **options)

    # A random name, not the product id.
    #
    # Caddy serves `/media/*` with `max-age=86400`, so a stable filename means a
    # replaced photo keeps showing the old one for a day — on the screen of the
    # person who just replaced it, which reads as the upload having failed. A
    # fresh name is a fresh URL and the question does not arise.
    return ContentFile(buffer.getvalue(), name=f"{secrets.token_hex(8)}.{extension}")


@receiver(pre_save, sender=Product)
def delete_superseded_image(sender, instance, **kwargs) -> None:
    """
    Remove the file a photo replaced.

    Django never deletes the old file when an `ImageField` is overwritten or
    cleared, so a menu whose photos are revised a few times leaves every
    previous version on the volume for good — on a host where that volume shares
    a disk with Postgres and the nightly backups.

    A signal rather than a call in the serializer, for the reason
    `apps/sync/receivers.py` gives at length: the admin, a management command
    and an endpoint nobody has written yet all have to be covered, and an
    explicit call at each write site is one refactor away from being missed.

    Deleted **after** the commit, never inside it. A rolled-back transaction
    that had already unlinked the file would leave the surviving row pointing at
    nothing — a broken photo with no explanation, which is a worse outcome than
    an orphaned file nobody sees.
    """
    if instance._state.adding:
        return

    # A targeted `save(update_fields=[...])` that does not touch the image
    # cannot have replaced it, so skip the query. This is the common case:
    # `sort_order`, `is_active` and the recipe cost rollup all save this way.
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "image" not in update_fields:
        return

    previous = sender.all_objects.filter(pk=instance.pk).values_list("image", flat=True).first()
    if not previous or previous == instance.image.name:
        return

    storage = instance.image.storage

    def _remove() -> None:
        try:
            storage.delete(previous)
        except OSError:
            # An orphaned file is a housekeeping matter; raising here would raise
            # it from inside an on_commit callback, after the client has already
            # been told the save succeeded.
            logger.warning("Replaced product image not deleted", extra={"media_path": previous})

    transaction.on_commit(_remove)
