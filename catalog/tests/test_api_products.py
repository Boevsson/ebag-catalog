import io
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from PIL import Image

from catalog.api.serializers import ProductSerializer
from catalog.models import Product
from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/products"


def detail_url(product: Product) -> str:
    return f"{LIST_URL}/{product.pk}"


def png(name: str = "photo.png", size: tuple[int, int] = (2, 2)) -> SimpleUploadedFile:
    """A real, tiny PNG (an ImageField rejects anything Pillow cannot open)."""
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.fixture
def category():
    return CategoryFactory(name="Dairy")


@pytest.fixture
def payload(category):
    return {
        "sku": "milk-1l",
        "title": "Fresh Milk 1L",
        "description": "3.6% fat",
        "price": "2.49",
        "category": category.pk,
    }


class TestReading:
    def test_list_is_public_and_paginated(self, api_client):
        ProductFactory.create_batch(3)

        response = api_client.get(LIST_URL)

        assert response.status_code == 200
        assert response.data["count"] == 3

    def test_list_does_not_query_per_product(self, api_client, django_assert_num_queries):
        ProductFactory.create_batch(5)

        with django_assert_num_queries(2):  # COUNT + the page (categories joined in)
            api_client.get(LIST_URL)

    def test_retrieve(self, api_client, category):
        product = ProductFactory(sku="MILK-1L", category=category)

        response = api_client.get(detail_url(product))

        assert response.status_code == 200
        assert response.data["sku"] == "MILK-1L"
        assert response.data["category_name"] == "Dairy"

    def test_unknown_product_is_404(self, api_client):
        assert api_client.get(f"{LIST_URL}/999999").status_code == 404


class TestCreating:
    def test_create(self, staff_client, payload):
        response = staff_client.post(LIST_URL, payload, format="json")

        assert response.status_code == 201
        assert response.data["sku"] == "MILK-1L"  # stored in canonical form
        assert response.data["price"] == "2.49"
        assert response["Location"].endswith(f"{LIST_URL}/{response.data['id']}")

    def test_description_is_optional(self, staff_client, payload):
        del payload["description"]

        assert staff_client.post(LIST_URL, payload, format="json").status_code == 201

    @pytest.mark.parametrize("field", ["sku", "title", "price", "category"])
    def test_required_fields(self, staff_client, payload, field):
        del payload[field]

        response = staff_client.post(LIST_URL, payload, format="json")

        assert response.status_code == 400
        assert field in response.data

    def test_duplicate_sku_is_detected_regardless_of_case(self, staff_client, payload):
        ProductFactory(sku="MILK-1L")

        response = staff_client.post(LIST_URL, payload, format="json")  # sends "milk-1l"

        assert response.status_code == 400
        assert "sku" in response.data

    def test_malformed_sku(self, staff_client, payload):
        response = staff_client.post(LIST_URL, payload | {"sku": "milk 1l"}, format="json")

        assert response.status_code == 400
        assert "sku" in response.data

    @pytest.mark.parametrize("price", ["-0.01", "2.499", "123456789.00", "free"])
    def test_invalid_price(self, staff_client, payload, price):
        response = staff_client.post(LIST_URL, payload | {"price": price}, format="json")

        assert response.status_code == 400
        assert "price" in response.data

    def test_unknown_category(self, staff_client, payload):
        response = staff_client.post(LIST_URL, payload | {"category": 999_999}, format="json")

        assert response.status_code == 400
        assert "category" in response.data


class TestUpdating:
    def test_patch(self, staff_client):
        product = ProductFactory(price=Decimal("1.00"))

        response = staff_client.patch(detail_url(product), {"price": "1.20"}, format="json")

        assert response.status_code == 200
        assert response.data["price"] == "1.20"

    def test_patch_leaves_the_fields_it_was_not_given_alone(self, staff_client):
        """
        Two requests read the same product; one changes the price, the other
        the title. Writing the whole row back would undo whichever came first.
        """
        product = ProductFactory(title="Oat milk", price=Decimal("2.00"))
        # The other request saves after this one has read the row (`product`
        # is now stale) and before it writes. A request cannot be paused halfway
        # through the view, hence the serializer instead of the client.
        Product.objects.filter(pk=product.pk).update(price=Decimal("2.50"))
        stale = ProductSerializer(product, data={"title": "Oat milk 1L"}, partial=True)
        stale.is_valid(raise_exception=True)

        stale.save()

        product.refresh_from_db()
        assert product.title == "Oat milk 1L"
        assert product.price == Decimal("2.50")

    def test_patch_still_moves_updated_at(self, staff_client):
        product = ProductFactory()
        before = product.updated_at

        staff_client.patch(detail_url(product), {"price": "1.20"}, format="json")

        product.refresh_from_db()
        assert product.updated_at > before

    def test_put(self, staff_client, payload):
        product = ProductFactory(sku="MILK-1L", title="Old title")

        response = staff_client.put(detail_url(product), payload, format="json")

        assert response.status_code == 200
        assert response.data["title"] == "Fresh Milk 1L"

    def test_sku_cannot_be_changed(self, staff_client):
        """Other systems (orders, warehouse) refer to a product by its SKU."""
        product = ProductFactory(sku="MILK-1L")

        response = staff_client.patch(detail_url(product), {"sku": "MILK-2L"}, format="json")

        assert response.status_code == 400
        assert "sku" in response.data

    def test_sending_the_same_sku_back_is_not_a_change(self, staff_client):
        product = ProductFactory(sku="MILK-1L")

        response = staff_client.patch(detail_url(product), {"sku": "milk-1l"}, format="json")

        assert response.status_code == 200


class TestDeleting:
    def test_delete(self, staff_client):
        product = ProductFactory()

        response = staff_client.delete(detail_url(product))

        assert response.status_code == 204
        assert not Product.objects.filter(pk=product.pk).exists()


class TestImage:
    def test_upload_on_create(self, staff_client, payload, settings):
        response = staff_client.post(LIST_URL, payload | {"image": png()}, format="multipart")

        assert response.status_code == 201
        assert response.data["image"].startswith("http://testserver/media/products/")
        stored = Product.objects.get(pk=response.data["id"]).image
        assert Path(settings.MEDIA_ROOT, stored.name).exists()
        assert "photo" not in stored.name  # the client's file name is not used

    def test_file_that_is_not_an_image(self, staff_client, payload):
        fake = SimpleUploadedFile("photo.png", b"#!/bin/sh\necho hi", content_type="image/png")

        response = staff_client.post(LIST_URL, payload | {"image": fake}, format="multipart")

        assert response.status_code == 400
        assert "image" in response.data

    def test_image_above_the_size_limit(self, staff_client, payload, settings):
        settings.PRODUCT_IMAGE_MAX_BYTES = 10

        response = staff_client.post(LIST_URL, payload | {"image": png()}, format="multipart")

        assert response.status_code == 400
        assert "image" in response.data

    def test_replacing_the_image_removes_the_old_file(
        self, staff_client, settings, django_capture_on_commit_callbacks
    ):
        product = ProductFactory(image=png("old.png"))
        old_file = Path(settings.MEDIA_ROOT, product.image.name)
        assert old_file.exists()

        # Files are removed only after the transaction commits, never before.
        with django_capture_on_commit_callbacks(execute=True):
            response = staff_client.patch(
                detail_url(product), {"image": png("new.png")}, format="multipart"
            )

        assert response.status_code == 200
        assert not old_file.exists()

    def test_image_can_be_removed_with_null(
        self, staff_client, settings, django_capture_on_commit_callbacks
    ):
        product = ProductFactory(image=png())
        old_file = Path(settings.MEDIA_ROOT, product.image.name)

        with django_capture_on_commit_callbacks(execute=True):
            response = staff_client.patch(detail_url(product), {"image": None}, format="json")

        assert response.status_code == 200
        assert response.data["image"] is None
        assert not old_file.exists()

    def test_upload_is_discarded_when_the_database_refuses_the_row(self, settings, category):
        """
        Django stores the file before the INSERT. When two requests race for one
        SKU, the loser's row is refused by the unique index - its file must go too.
        """
        ProductFactory(sku="MILK-1L")
        loser = Product(
            sku="MILK-1L", title="Milk", price=Decimal("2.49"), category=category, image=png()
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            loser.save()

        assert list(Path(settings.MEDIA_ROOT).rglob("*.png")) == []

    def test_refused_update_keeps_the_image_the_product_already_has(self, settings):
        product = ProductFactory(image=png("old.png"))
        old_file = Path(settings.MEDIA_ROOT, product.image.name)

        product.image = png("new.png")
        product.price = Decimal("-1")  # refused by the CHECK constraint
        with pytest.raises(IntegrityError), transaction.atomic():
            product.save()

        assert list(Path(settings.MEDIA_ROOT).rglob("*.png")) == [old_file]

    def test_refused_update_without_an_upload_touches_no_file(self, settings):
        """Only a file stored by the failed save is discarded - never one that was already there."""
        product = ProductFactory(image=png())
        file = Path(settings.MEDIA_ROOT, product.image.name)

        product.price = Decimal("-1")
        with pytest.raises(IntegrityError), transaction.atomic():
            product.save()

        assert file.exists()

    def test_deleting_the_product_removes_its_file(
        self, staff_client, settings, django_capture_on_commit_callbacks
    ):
        product = ProductFactory(image=png())
        file = Path(settings.MEDIA_ROOT, product.image.name)

        with django_capture_on_commit_callbacks(execute=True):
            staff_client.delete(detail_url(product))

        assert not file.exists()
