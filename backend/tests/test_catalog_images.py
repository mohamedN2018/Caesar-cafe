"""
Product photographs.

The screen that uploads these tells the manager the server checks the file too.
It did not — `ImageField` verified that the bytes decoded as an image and nothing
else — so a four-thousand-pixel phone photo was stored and then served, at full
size, to the POS grid that loads the whole menu in one request. These tests are
what makes that sentence in the UI true.

What they defend, in order of how much a mistake costs:

  * a photo the till cannot afford to download never gets stored, whatever was
    uploaded;
  * a portrait photo stays portrait — the rotation lives in a metadata tag, and
    the re-encode that strips the metadata has to bake it in first;
  * nothing the camera wrote about where it was standing reaches a file Caddy
    serves to anyone with the URL;
  * an oversized file is a 400 with a reason a manager can act on, not a 500;
  * replacing a photo removes the one it replaced, and changes the URL, because
    `/media/` is cached for a day.

The images here are built from a small block of seeded pseudo-random colour
scaled up smoothly. Flat colour would compress to almost nothing and make every
size assertion vacuous; full-resolution noise is the opposite problem, since it
is incompressible and a 4000x3000 frame of it would exceed the upload limit
under test. A blurred gradient is what a photograph actually looks like to an
encoder.
"""

from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog import images
from apps.catalog.models import Category, Product

pytestmark = pytest.mark.django_db

#: Fixed, so a failure is the same failure tomorrow.
SEED = 20260811


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    """
    A media root per test.

    Without it every upload lands in the developer's working tree, and the
    deletion tests operate on whatever a previous run left behind.
    """
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def owner(make_user):
    return make_user(email="owner@caesar.test", role="SUPER_ADMIN")


@pytest.fixture
def client(authed, owner, branch):
    return authed(owner, branch=branch)


@pytest.fixture
def product(organization, branch) -> Product:
    category = Category.objects.create(
        organization=organization, branch=branch, name_ar="مشروبات ساخنة"
    )
    return Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )


def frame(size: tuple[int, int], *, mode: str = "RGB") -> Image.Image:
    rng = random.Random(SEED)
    channels = len(Image.new(mode, (1, 1)).getbands())
    block = (48, 36)
    seed = Image.frombytes(
        mode,
        block,
        bytes(rng.randrange(256) for _ in range(block[0] * block[1] * channels)),
    )
    return seed.resize(size, Image.Resampling.BICUBIC)


def photo(
    *,
    size: tuple[int, int] = (1600, 1200),
    image_format: str = "JPEG",
    exif: Image.Exif | None = None,
) -> SimpleUploadedFile:
    buffer = BytesIO()
    options = {"exif": exif.tobytes()} if exif is not None else {}
    frame(size).save(buffer, format=image_format, **options)

    extension = "jpg" if image_format == "JPEG" else image_format.lower()
    mime = "jpeg" if image_format == "JPEG" else image_format.lower()
    return SimpleUploadedFile(
        f"upload.{extension}", buffer.getvalue(), content_type=f"image/{mime}"
    )


def upload(client, product, file):
    return client.patch(
        f"/api/v1/catalog/products/{product.id}/", {"image": file}, format="multipart"
    )


def stored(product: Product) -> Image.Image:
    product.refresh_from_db()
    return Image.open(product.image.path)


# ── what gets stored ─────────────────────────────────────────────────────────


class TestNormalisation:
    def test_a_large_photo_is_bounded_before_it_is_stored(self, client, product) -> None:
        response = upload(client, product, photo(size=(4000, 3000)))
        assert response.status_code == 200

        image = stored(product)
        assert max(image.size) == images.MAX_EDGE
        # 4:3 in, 4:3 out. A stretched photo is a different kind of wrong from an
        # oversized one, and just as visible on the tile.
        assert image.size == (images.MAX_EDGE, images.MAX_EDGE * 3 // 4)

    def test_a_small_photo_is_not_enlarged(self, client, product) -> None:
        """Upscaling spends bytes inventing detail that was never photographed."""
        upload(client, product, photo(size=(240, 240)))
        assert stored(product).size == (240, 240)

    def test_the_stored_file_is_much_smaller_than_what_was_uploaded(
        self, client, product
    ) -> None:
        """
        The whole point of this module. The till fetches the entire menu in one
        request over a mobile connection, so the number that matters is bytes on
        the wire — not pixels, and not whether the upload succeeded.
        """
        source = photo(size=(4000, 3000))
        uploaded_bytes = source.size

        upload(client, product, source)
        product.refresh_from_db()

        # Twenty times fewer pixels. A third is a floor, not an expectation.
        assert product.image.size < uploaded_bytes / 3

    def test_the_upload_is_re_encoded_rather_than_stored_as_sent(
        self, client, product
    ) -> None:
        upload(client, product, photo(image_format="PNG", size=(1200, 1200)))
        product.refresh_from_db()

        assert Path(product.image.name).suffix in (".webp", ".jpg")
        assert stored(product).format in ("WEBP", "JPEG")

    def test_a_transparent_cutout_does_not_come_out_black(self, client, product) -> None:
        """
        JPEG has no alpha channel, and `convert("RGB")` renders every transparent
        pixel black — a cut-out arrives as a silhouette. WebP keeps the alpha;
        the JPEG fallback flattens onto white. Either is fine. Black is not.
        """
        buffer = BytesIO()
        Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(buffer, format="PNG")
        upload(
            client,
            product,
            SimpleUploadedFile("cutout.png", buffer.getvalue(), content_type="image/png"),
        )

        corner = stored(product).convert("RGBA").getpixel((0, 0))
        # A tolerance on the alpha, not on the colour: lossy WebP encodes the
        # alpha plane separately and a constant one comes back within a step or
        # two. Under a transparent pixel the RGB is undefined, so it is only
        # checked on the branch where there is no alpha left to read.
        assert corner[3] < 16 or corner[:3] == (255, 255, 255)


class TestMetadata:
    def _exif(self) -> Image.Exif:
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display
        exif[0x010F] = "ACME"  # Make
        exif[0x0110] = "Phone 12"  # Model
        return exif

    def test_a_rotated_photo_is_stored_the_way_up_it_was_taken(
        self, client, product
    ) -> None:
        """
        Orientation 6 means "rotate 90° clockwise to display". A phone writes the
        tag rather than rotating the pixels, and the re-encode drops the tag — so
        unless the rotation is applied first, every portrait photo taken on a
        phone arrives on its side, permanently.
        """
        upload(client, product, photo(size=(1200, 800), exif=self._exif()))

        width, height = stored(product).size
        assert height > width, "the orientation tag was dropped without being applied"

    def test_what_the_camera_wrote_does_not_reach_the_public_file(
        self, client, product
    ) -> None:
        """
        `/media/` is served by Caddy to anyone holding the URL, with a day of
        cache. A phone's EXIF block carries the GPS fix of wherever the picture
        was taken and the handset's make and model, and nothing in this product
        reads any of it. Asserting the block is empty rather than picking off
        individual tags is deliberate: a tag-by-tag check passes the day a
        camera writes one nobody thought of.
        """
        upload(client, product, photo(size=(1200, 800), exif=self._exif()))

        image = stored(product)
        assert not dict(image.getexif())
        assert "exif" not in image.info


# ── what gets refused ────────────────────────────────────────────────────────


class TestRefusals:
    def test_a_file_over_the_limit_is_refused_with_a_reason(self, client, product) -> None:
        # Deliberately not a valid image. The size gate has to answer first, or
        # a 40MB upload gets reported as "not a valid image" — a true statement
        # that sends the manager looking for the wrong problem — and, worse, a
        # valid 40MB one gets fully decoded before anything refuses it.
        oversized = SimpleUploadedFile(
            "huge.jpg",
            b"\xff\xd8\xff" + b"\x00" * images.MAX_UPLOAD_BYTES,
            content_type="image/jpeg",
        )

        response = upload(client, product, oversized)

        assert response.status_code == 400
        # The specific code, not a generic VALIDATION_ERROR. The upload button
        # shows `message`, and the envelope puts a field-level serializer error
        # in `errors` behind the generic "the submitted data is incorrect" —
        # which does not tell a manager to pick a smaller photo.
        assert response.data["code"] == "IMAGE_TOO_LARGE"
        product.refresh_from_db()
        assert not product.image

    def test_a_truncated_image_is_a_400_not_a_500(self, client, product) -> None:
        """
        A header that parses and pixels that do not — an upload cut off
        mid-transfer. Pillow's `open()` is lazy, so without the explicit
        `load()` this surfaces from inside the resize as an unhandled exception,
        and the manager is told the server broke rather than the file did.
        """
        whole = photo(size=(1200, 900)).read()
        truncated = SimpleUploadedFile(
            "cut.jpg", whole[: len(whole) // 3], content_type="image/jpeg"
        )

        response = upload(client, product, truncated)

        assert response.status_code == 400
        product.refresh_from_db()
        assert not product.image

    def test_something_that_is_not_an_image_at_all_is_refused(
        self, client, product
    ) -> None:
        text = SimpleUploadedFile(
            "menu.jpg", b"this is a spreadsheet, actually" * 50, content_type="image/jpeg"
        )

        response = upload(client, product, text)

        assert response.status_code == 400
        product.refresh_from_db()
        assert not product.image

    def test_a_cashier_cannot_put_a_photo_on_the_menu(
        self, authed, make_user, branch, product
    ) -> None:
        """Whoever may edit a product may put a face on it, and nobody else."""
        cashier = make_user(email="cashier@caesar.test", role="CASHIER", branch=branch)

        response = upload(authed(cashier, branch=branch), product, photo(size=(400, 400)))

        assert response.status_code == 403


# ── replacing and clearing ───────────────────────────────────────────────────


class TestSupersededFiles:
    def test_replacing_a_photo_deletes_the_one_it_replaced(self, client, product) -> None:
        """
        Django never removes the old file. A menu revised a few times would leave
        every previous version on a volume that shares a disk with Postgres and
        the nightly backups.
        """
        upload(client, product, photo(size=(800, 600)))
        product.refresh_from_db()
        first = Path(product.image.path)
        assert first.exists()

        upload(client, product, photo(size=(700, 500)))
        product.refresh_from_db()

        assert not first.exists()
        assert Path(product.image.path).exists()

    def test_the_url_changes_so_a_replacement_is_visible_the_same_day(
        self, client, product
    ) -> None:
        """
        Caddy serves `/media/*` with `max-age=86400`. A stable filename means the
        person who just replaced the photo keeps seeing the old one, which reads
        as the upload having failed rather than as a cache.
        """
        upload(client, product, photo(size=(800, 600)))
        product.refresh_from_db()
        first = product.image.name

        upload(client, product, photo(size=(800, 600)))
        product.refresh_from_db()

        assert product.image.name != first

    def test_clearing_the_photo_empties_the_field_and_removes_the_file(
        self, client, product
    ) -> None:
        """
        An empty multipart value, which is the only way a form can say "none" —
        a multipart body has no way to carry a JSON null.
        """
        upload(client, product, photo(size=(800, 600)))
        product.refresh_from_db()
        path = Path(product.image.path)

        response = client.patch(
            f"/api/v1/catalog/products/{product.id}/", {"image": ""}, format="multipart"
        )

        assert response.status_code == 200
        product.refresh_from_db()
        assert not product.image
        assert not path.exists()

    def test_an_ordinary_edit_leaves_the_photo_alone(self, client, product) -> None:
        """
        The receiver runs on every save of a product, so a rename must not take
        the photo with it — and neither must a targeted
        `save(update_fields=[...])`, which is how the recipe cost rollup and the
        sort order are written.
        """
        upload(client, product, photo(size=(800, 600)))
        product.refresh_from_db()
        path = Path(product.image.path)
        name = product.image.name

        client.patch(
            f"/api/v1/catalog/products/{product.id}/",
            {"name_ar": "كابتشينو مزدوج"},
            format="json",
        )
        product.refresh_from_db()
        product.sort_order = 5
        product.save(update_fields=["sort_order"])

        product.refresh_from_db()
        assert product.image.name == name
        assert path.exists()
