"""
Product images and the storage backend.

The application never touches the file system itself: models, serializers and
the clean-up of old files all go through Django's storage API. That is what
makes "upload to a bucket instead of the local disk" a configuration change.

TestApplicationIsStorageAgnostic proves it with a storage that has nothing to
do with the disk. TestGoogleCloudStorageBackend checks the bundled Google
driver as far as that is possible without a real bucket: it is installed, it
accepts exactly the options the settings produce, and it builds the right URLs.
No network is used.
"""

import io
import runpy
from pathlib import Path

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

import config.settings
from catalog.models import Product
from catalog.tests.factories import CategoryFactory

PRODUCTS_URL = "/api/v1/products"


def png(name: str = "photo.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.mark.django_db
class TestApplicationIsStorageAgnostic:
    @pytest.fixture(autouse=True)
    def bucket_like_storage(self, settings):
        """Not the disk, and with its own public URL - like a bucket behind a CDN."""
        settings.STORAGES = {
            **settings.STORAGES,
            "default": {
                "BACKEND": "django.core.files.storage.InMemoryStorage",
                "OPTIONS": {"base_url": "https://cdn.example.test/"},
            },
        }

    @pytest.fixture
    def payload(self):
        return {
            "sku": "MILK-1L",
            "title": "Fresh Milk",
            "price": "1.49",
            "category": CategoryFactory().pk,
        }

    def upload(self, staff_client, payload) -> Product:
        response = staff_client.post(PRODUCTS_URL, payload | {"image": png()}, format="multipart")
        assert response.status_code == 201
        self.response = response
        return Product.objects.get(pk=response.data["id"])

    def test_the_file_goes_to_the_configured_storage_not_to_the_disk(
        self, staff_client, payload, settings
    ):
        product = self.upload(staff_client, payload)

        assert default_storage.exists(product.image.name)
        assert not Path(settings.MEDIA_ROOT, product.image.name).exists()

    def test_the_api_returns_the_url_the_storage_gives(self, staff_client, payload):
        product = self.upload(staff_client, payload)

        assert self.response.data["image"] == f"https://cdn.example.test/{product.image.name}"

    def test_replaced_and_deleted_images_are_removed_from_that_storage(
        self, staff_client, payload, django_capture_on_commit_callbacks
    ):
        product = self.upload(staff_client, payload)
        first = product.image.name

        with django_capture_on_commit_callbacks(execute=True):
            staff_client.patch(
                f"{PRODUCTS_URL}/{product.pk}", {"image": png("new.png")}, format="multipart"
            )
        product.refresh_from_db()
        second = product.image.name

        assert not default_storage.exists(first)
        assert default_storage.exists(second)

        with django_capture_on_commit_callbacks(execute=True):
            staff_client.delete(f"{PRODUCTS_URL}/{product.pk}")

        assert not default_storage.exists(second)


class TestGoogleCloudStorageBackend:
    @pytest.fixture
    def storage_for(self, monkeypatch):
        """The backend, built from the options config/settings.py produces for an environment."""
        from google.auth.credentials import AnonymousCredentials
        from storages.backends.gcloud import GoogleCloudStorage

        def build(**environment: str) -> GoogleCloudStorage:
            for name, value in environment.items():
                monkeypatch.setenv(name, value)
            options = runpy.run_path(config.settings.__file__)["STORAGES"]["default"]["OPTIONS"]
            # Anonymous credentials: nothing here may need a real Google account.
            return GoogleCloudStorage(
                **options, credentials=AnonymousCredentials(), project_id="test"
            )

        return build

    def test_the_driver_accepts_every_option_the_settings_produce(self, storage_for):
        """django-storages refuses unknown option names, so a typo fails here."""
        storage = storage_for(MEDIA_STORAGE="gcs", GCS_BUCKET_NAME="shop-media")

        assert storage.bucket_name == "shop-media"

    def test_urls_point_at_the_public_bucket(self, storage_for):
        storage = storage_for(MEDIA_STORAGE="gcs", GCS_BUCKET_NAME="shop-media")

        assert (
            storage.url("products/abc.png")
            == "https://storage.googleapis.com/shop-media/products/abc.png"
        )

    def test_urls_point_at_the_cdn_when_one_is_configured(self, storage_for):
        storage = storage_for(
            MEDIA_STORAGE="gcs",
            GCS_BUCKET_NAME="shop-media",
            GCS_CDN_URL="https://cdn.example.com",
        )

        assert storage.url("products/abc.png") == "https://cdn.example.com/products/abc.png"

    def test_a_folder_inside_the_bucket(self, storage_for):
        storage = storage_for(
            MEDIA_STORAGE="gcs", GCS_BUCKET_NAME="shop-media", GCS_LOCATION="catalog"
        )

        assert storage.url("products/abc.png").endswith("/shop-media/catalog/products/abc.png")
