import pytest

from catalog.api.serializers import CategorySerializer
from catalog.models import Category
from catalog.services.categories import update_category
from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/categories"


def detail_url(category: Category) -> str:
    return f"{LIST_URL}/{category.pk}"


class TestReading:
    def test_list_is_public_and_paginated(self, api_client):
        CategoryFactory.create_batch(3)

        response = api_client.get(LIST_URL)

        assert response.status_code == 200
        assert response.data["count"] == 3

    def test_representation(self, api_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)

        response = api_client.get(detail_url(dairy))

        assert response.status_code == 200
        assert response.data["id"] == dairy.pk
        assert response.data["name"] == "Dairy"
        assert response.data["parent"] == food.pk
        assert response.data["path"] == f"/{food.pk}/{dairy.pk}/"

    def test_list_can_be_limited_to_the_children_of_one_category(self, api_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)
        CategoryFactory(name="Milk", parent=dairy)

        response = api_client.get(LIST_URL, {"parent": food.pk})

        assert [category["name"] for category in response.data["results"]] == ["Dairy"]

    def test_list_can_be_limited_to_top_level_categories(self, api_client):
        food = CategoryFactory(name="Food")
        CategoryFactory(name="Dairy", parent=food)

        response = api_client.get(LIST_URL, {"parent": "root"})

        assert [category["name"] for category in response.data["results"]] == ["Food"]

    @pytest.mark.parametrize("value", ["dairy", "-1", "1.5"])
    def test_parent_filter_that_is_not_an_id_is_rejected(self, api_client, value):
        response = api_client.get(LIST_URL, {"parent": value})

        assert response.status_code == 400
        assert "parent" in response.data

    def test_unknown_category_is_404(self, api_client):
        assert api_client.get(f"{LIST_URL}/999999").status_code == 404


class TestCreating:
    def test_top_level_category(self, staff_client):
        response = staff_client.post(LIST_URL, {"name": "Food"}, format="json")

        assert response.status_code == 201
        assert response.data["parent"] is None
        assert response.data["path"] == f"/{response.data['id']}/"
        assert response["Location"].endswith(f"{LIST_URL}/{response.data['id']}")

    def test_sub_category(self, staff_client):
        food = CategoryFactory(name="Food")

        response = staff_client.post(LIST_URL, {"name": "Dairy", "parent": food.pk}, format="json")

        assert response.status_code == 201
        assert response.data["path"] == f"/{food.pk}/{response.data['id']}/"

    def test_duplicate_sibling_name_is_a_field_error(self, staff_client):
        food = CategoryFactory(name="Food")
        CategoryFactory(name="Dairy", parent=food)

        response = staff_client.post(LIST_URL, {"name": "dairy", "parent": food.pk}, format="json")

        assert response.status_code == 400
        assert "name" in response.data

    def test_same_name_under_another_parent_is_fine(self, staff_client):
        food, drinks = CategoryFactory(name="Food"), CategoryFactory(name="Drinks")
        CategoryFactory(name="Organic", parent=food)

        response = staff_client.post(
            LIST_URL, {"name": "Organic", "parent": drinks.pk}, format="json"
        )

        assert response.status_code == 201

    @pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 121}])
    def test_invalid_name(self, staff_client, payload):
        response = staff_client.post(LIST_URL, payload, format="json")

        assert response.status_code == 400
        assert "name" in response.data

    def test_unknown_parent(self, staff_client):
        response = staff_client.post(LIST_URL, {"name": "Dairy", "parent": 999_999}, format="json")

        assert response.status_code == 400
        assert "parent" in response.data

    def test_path_cannot_be_set_by_the_client(self, staff_client):
        response = staff_client.post(LIST_URL, {"name": "Food", "path": "/42/"}, format="json")

        assert response.data["path"] == f"/{response.data['id']}/"


class TestUpdating:
    def test_rename_with_patch(self, staff_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)

        response = staff_client.patch(detail_url(dairy), {"name": "Dairy & Eggs"}, format="json")

        assert response.status_code == 200
        assert response.data["name"] == "Dairy & Eggs"
        assert response.data["parent"] == food.pk  # untouched by a partial update

    def test_move_with_patch_takes_the_subtree_along(self, staff_client, api_client):
        food, drinks = CategoryFactory(name="Food"), CategoryFactory(name="Drinks")
        dairy = CategoryFactory(name="Dairy", parent=food)
        milk = CategoryFactory(name="Milk", parent=dairy)

        response = staff_client.patch(detail_url(dairy), {"parent": drinks.pk}, format="json")

        assert response.status_code == 200
        assert response.data["path"] == f"/{drinks.pk}/{dairy.pk}/"
        assert (
            api_client.get(detail_url(milk)).data["path"] == f"/{drinks.pk}/{dairy.pk}/{milk.pk}/"
        )

    def test_move_to_the_top_level_with_null_parent(self, staff_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)

        response = staff_client.patch(detail_url(dairy), {"parent": None}, format="json")

        assert response.status_code == 200
        assert response.data["path"] == f"/{dairy.pk}/"

    def test_put_replaces_the_whole_state(self, staff_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)

        response = staff_client.put(detail_url(dairy), {"name": "Dairy"}, format="json")

        assert response.status_code == 200
        assert response.data["parent"] is None  # omitted in a PUT means "no parent"

    def test_patch_does_not_write_back_fields_it_was_not_given(self):
        """
        The view reads the category, then the service waits for its lock. If
        another request moved the category in between, a PATCH that only
        renames must not move it back: the fields it did not send are not its
        business, and its copy of them is out of date.
        """
        food = CategoryFactory(name="Food")
        drinks = CategoryFactory(name="Drinks")
        dairy = CategoryFactory(name="Dairy", parent=food)
        read_by_the_view = Category.objects.get(pk=dairy.pk)

        update_category(dairy, parent=drinks)  # the other request wins the lock first
        rename = CategorySerializer(read_by_the_view, data={"name": "Dairy & Eggs"}, partial=True)
        rename.is_valid(raise_exception=True)
        saved = rename.save()

        assert saved.name == "Dairy & Eggs"
        assert saved.parent == drinks
        assert Category.objects.get(pk=dairy.pk).parent == drinks

    def test_move_under_own_descendant_is_a_field_error(self, staff_client):
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)

        response = staff_client.patch(detail_url(food), {"parent": dairy.pk}, format="json")

        assert response.status_code == 400
        assert "parent" in response.data

    def test_rename_to_a_sibling_name_is_a_field_error(self, staff_client):
        CategoryFactory(name="Food")
        drinks = CategoryFactory(name="Drinks")

        response = staff_client.patch(detail_url(drinks), {"name": "Food"}, format="json")

        assert response.status_code == 400
        assert "name" in response.data


class TestDeleting:
    def test_empty_category(self, staff_client):
        food = CategoryFactory(name="Food")

        response = staff_client.delete(detail_url(food))

        assert response.status_code == 204
        assert not Category.objects.filter(pk=food.pk).exists()

    def test_category_with_sub_categories_is_a_conflict(self, staff_client):
        food = CategoryFactory(name="Food")
        CategoryFactory(name="Dairy", parent=food)

        response = staff_client.delete(detail_url(food))

        assert response.status_code == 409
        assert "sub-categories" in response.data["detail"]

    def test_category_with_products_is_a_conflict(self, staff_client):
        food = CategoryFactory(name="Food")
        ProductFactory(category=food)

        response = staff_client.delete(detail_url(food))

        assert response.status_code == 409
        assert "products" in response.data["detail"]
