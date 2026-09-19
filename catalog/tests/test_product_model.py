from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from catalog.models.product import product_image_path
from catalog.tests.factories import ProductFactory
from catalog.validators import normalize_sku, validate_sku


class TestSku:
    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [("ab-12", "AB-12"), ("  AB-12 ", "AB-12"), ("Milk.1L_x", "MILK.1L_X")],
    )
    def test_normalize_sku(self, raw, normalized):
        assert normalize_sku(raw) == normalized

    @pytest.mark.parametrize("sku", ["A", "AB-12", "3800123456789", "MILK.1L_X", "a1", "X" * 64])
    def test_valid_skus(self, sku):
        validate_sku(sku)  # does not raise

    @pytest.mark.parametrize(
        "sku",
        ["", " ", "-AB", "AB-", "AB 12", "AB/12", "МЛЯКО-1", "X" * 65],
        ids=[
            "empty",
            "blank",
            "leading-dash",
            "trailing-dash",
            "space",
            "slash",
            "cyrillic",
            "long",
        ],
    )
    def test_invalid_skus(self, sku):
        with pytest.raises(ValidationError):
            validate_sku(sku)

    @pytest.mark.django_db
    def test_sku_is_stored_normalized_whatever_the_entry_point(self):
        product = ProductFactory(sku="  ab-12 ")

        product.refresh_from_db()
        assert product.sku == "AB-12"

    @pytest.mark.django_db
    def test_sku_is_unique_regardless_of_case(self):
        ProductFactory(sku="AB-12")

        with pytest.raises(IntegrityError), transaction.atomic():
            ProductFactory(sku="ab-12")


@pytest.mark.django_db
class TestPrice:
    def test_database_rejects_a_negative_price(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            ProductFactory(price=Decimal("-0.01"))

    def test_zero_is_a_valid_price(self):
        assert ProductFactory(price=Decimal("0")).price == Decimal("0")


class TestDisplay:
    def test_is_displayed_by_sku_and_title(self):
        assert str(ProductFactory.build(sku="MILK-1L", title="Fresh Milk")) == "MILK-1L Fresh Milk"


class TestImagePath:
    def test_client_file_name_is_not_trusted(self):
        path = product_image_path(None, "../../etc/My Holiday Photo.JPG")

        assert path.startswith("products/")
        assert path.endswith(".jpg")
        assert "Holiday" not in path
        assert ".." not in path

    def test_every_upload_gets_its_own_name(self):
        assert product_image_path(None, "milk.png") != product_image_path(None, "milk.png")
