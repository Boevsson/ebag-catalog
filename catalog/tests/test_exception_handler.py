"""
catalog_exception_handler: errors DRF does not know about become proper responses.

TestMapping calls the handler directly (no database, no HTTP).
TestThroughTheApi provokes the two situations that are hard to reach from the
outside - both are races - and checks the response a client would really get.
"""

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from rest_framework.exceptions import NotFound
from rest_framework.validators import UniqueValidator

from catalog.api.exception_handler import catalog_exception_handler
from catalog.exceptions import CatalogError, CategoryInUse
from catalog.models import Category
from catalog.tests.factories import CategoryFactory, ProductFactory


class TestMapping:
    @pytest.mark.parametrize(
        ("error", "status_code"),
        [
            (CategoryInUse('"Milk" still has products.'), 409),
            (CatalogError("a domain rule without a mapping of its own"), 400),
            (ObjectDoesNotExist(), 404),
            (IntegrityError("(1062, \"Duplicate entry 'AB-1' for key 'sku'\")"), 409),
        ],
        ids=["category-in-use", "other-domain-error", "object-vanished", "integrity-error"],
    )
    def test_status_code(self, error, status_code):
        response = catalog_exception_handler(error, {})

        assert response.status_code == status_code
        assert set(response.data) == {"detail"}  # same shape as DRF's own errors

    def test_domain_errors_explain_themselves_to_the_client(self):
        response = catalog_exception_handler(CategoryInUse('"Milk" still has products.'), {})

        assert response.data["detail"] == '"Milk" still has products.'

    def test_database_details_are_not_leaked_to_the_client(self):
        error = IntegrityError("(1062, \"Duplicate entry 'AB-1' for key 'sku'\")")

        response = catalog_exception_handler(error, {})

        assert "Duplicate entry" not in response.data["detail"]
        assert "sku" not in response.data["detail"]

    def test_errors_drf_knows_are_left_to_drf(self):
        assert catalog_exception_handler(NotFound(), {}).status_code == 404

    def test_anything_else_is_a_bug_and_stays_a_500(self):
        """Returning None tells DRF to re-raise, so Django logs it and answers 500."""
        assert catalog_exception_handler(RuntimeError("a bug"), {}) is None


@pytest.mark.django_db
class TestThroughTheApi:
    def test_two_requests_racing_for_the_same_sku(self, staff_client, monkeypatch):
        """
        Both requests pass validation (neither sees the other's uncommitted
        row); the unique index stops the second one. Simulated by blinding the
        validator, so the database is what rejects the duplicate.
        """
        existing = ProductFactory(sku="MILK-1L")
        monkeypatch.setattr(UniqueValidator, "__call__", lambda *args, **kwargs: None)

        response = staff_client.post(
            "/api/v1/products",
            {"sku": "MILK-1L", "title": "Milk", "price": "1.00", "category": existing.category_id},
            format="json",
        )

        assert response.status_code == 409  # not a 500
        assert "retry" in response.data["detail"].lower()

    def test_category_deleted_while_the_request_waited_for_the_tree_lock(
        self, staff_client, monkeypatch
    ):
        """The view found it, but it is gone when the service re-reads it under the lock."""
        category = CategoryFactory(name="Food")

        def vanished(self, *args, **kwargs):
            raise Category.DoesNotExist

        monkeypatch.setattr(Category, "refresh_from_db", vanished)

        response = staff_client.patch(
            f"/api/v1/categories/{category.pk}", {"name": "Groceries"}, format="json"
        )

        assert response.status_code == 404
