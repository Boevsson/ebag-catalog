"""
GET /api/v1/products/search - the HTTP face of catalog.search.

What matches what is covered in test_product_search.py. These tests cover what
only exists at the HTTP level: parsing the query string, error responses,
pagination and the response shape.
"""

from decimal import Decimal

import pytest

from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/products/search"


@pytest.fixture
def dairy():
    return CategoryFactory(name="Dairy")


@pytest.fixture(autouse=True)
def catalog(dairy):
    milk = CategoryFactory(name="Milk", parent=dairy)
    ProductFactory(sku="MILK-1L", title="Fresh Milk 1L", price=Decimal("2.49"), category=milk)
    ProductFactory(sku="MILK-OAT", title="Oat Milk 1L", price=Decimal("3.99"), category=milk)
    ProductFactory(sku="BUTTER", title="Butter 250g", price=Decimal("4.10"), category=dairy)
    ProductFactory(sku="WATER", title="Mineral Water", price=Decimal("0.89"))


def skus(response) -> list[str]:
    return [product["sku"] for product in response.data["results"]]


class TestSearching:
    def test_is_public(self, api_client):
        assert api_client.get(URL).status_code == 200

    def test_without_parameters_lists_everything(self, api_client):
        assert api_client.get(URL).data["count"] == 4

    def test_by_text(self, api_client):
        assert skus(api_client.get(URL, {"q": "milk"})) == ["MILK-1L", "MILK-OAT"]

    def test_by_sku(self, api_client):
        assert skus(api_client.get(URL, {"sku": "butter"})) == ["BUTTER"]

    def test_by_price_range(self, api_client):
        response = api_client.get(URL, {"min_price": "2.49", "max_price": "4.00"})

        assert skus(response) == ["MILK-1L", "MILK-OAT"]

    def test_by_category_includes_sub_categories(self, api_client, dairy):
        response = api_client.get(URL, {"category": dairy.pk})

        assert set(skus(response)) == {"MILK-1L", "MILK-OAT", "BUTTER"}

    def test_all_parameters_combined(self, api_client, dairy):
        response = api_client.get(
            URL,
            {
                "q": "milk",
                "min_price": "3",
                "max_price": "10",
                "category": dairy.pk,
                "ordering": "-price",
            },
        )

        assert skus(response) == ["MILK-OAT"]

    def test_ordering(self, api_client):
        assert skus(api_client.get(URL, {"ordering": "price"}))[0] == "WATER"
        assert skus(api_client.get(URL, {"ordering": "-price"}))[0] == "BUTTER"

    def test_empty_parameters_are_treated_as_absent(self, api_client):
        """HTML forms send `?q=&min_price=` for fields the user left empty."""
        response = api_client.get(URL, {"q": "", "min_price": "", "category": "", "ordering": ""})

        assert response.status_code == 200
        assert response.data["count"] == 4

    def test_whitespace_only_text_is_treated_as_absent_too(self, api_client):
        """Someone pressed space and Enter in the search box: not an error."""
        response = api_client.get(URL, {"q": "   ", "sku": " "})

        assert response.status_code == 200
        assert response.data["count"] == 4


class TestResponseShape:
    def test_page_envelope(self, api_client):
        response = api_client.get(URL)

        assert set(response.data) == {"count", "next", "previous", "results"}

    def test_product_representation(self, api_client, dairy):
        product = api_client.get(URL, {"sku": "BUTTER"}).data["results"][0]

        assert product["sku"] == "BUTTER"
        assert product["title"] == "Butter 250g"
        assert product["price"] == "4.10"  # a string: JSON numbers are floats
        assert product["category"] == dairy.pk
        assert product["category_name"] == "Dairy"
        assert product["image"] is None
        assert {"id", "description", "created_at", "updated_at"} <= set(product)


class TestInvalidParameters:
    def test_minimum_above_maximum(self, api_client):
        response = api_client.get(URL, {"min_price": "10", "max_price": "5"})

        assert response.status_code == 400
        assert "max_price" in response.data

    @pytest.mark.parametrize("parameter", ["min_price", "max_price"])
    def test_negative_price(self, api_client, parameter):
        response = api_client.get(URL, {parameter: "-1"})

        assert response.status_code == 400
        assert parameter in response.data

    @pytest.mark.parametrize("value", ["cheap", "1,50", "1.999"])
    def test_malformed_price(self, api_client, value):
        response = api_client.get(URL, {"max_price": value})

        assert response.status_code == 400
        assert "max_price" in response.data

    def test_unknown_category_is_an_error_not_an_empty_result(self, api_client):
        """An id that does not exist is a client bug; hiding it behind [] helps nobody."""
        response = api_client.get(URL, {"category": 999_999})

        assert response.status_code == 400
        assert "category" in response.data

    def test_malformed_category(self, api_client):
        assert api_client.get(URL, {"category": "dairy"}).status_code == 400

    def test_unknown_ordering(self, api_client):
        response = api_client.get(URL, {"ordering": "sku; DROP TABLE"})

        assert response.status_code == 400
        assert "ordering" in response.data

    def test_overlong_text(self, api_client):
        response = api_client.get(URL, {"q": "x" * 101})

        assert response.status_code == 400
        assert "q" in response.data

    def test_too_many_words(self, api_client):
        """Every word costs two LIKE conditions, so their number is capped."""
        at_the_limit = api_client.get(URL, {"q": " ".join(["milk"] * 10)})
        over_the_limit = api_client.get(URL, {"q": " ".join(["milk"] * 11)})

        assert at_the_limit.status_code == 200
        assert over_the_limit.status_code == 400
        assert "q" in over_the_limit.data

    def test_every_problem_is_reported_at_once(self, api_client):
        response = api_client.get(URL, {"min_price": "-1", "ordering": "random"})

        assert {"min_price", "ordering"} <= set(response.data)


class TestPagination:
    def test_page_size_and_page(self, api_client):
        first = api_client.get(URL, {"ordering": "price", "page_size": 3})
        second = api_client.get(URL, {"ordering": "price", "page_size": 3, "page": 2})

        assert first.data["count"] == 4
        assert skus(first) == ["WATER", "MILK-1L", "MILK-OAT"]
        assert first.data["next"] is not None
        assert skus(second) == ["BUTTER"]
        assert second.data["next"] is None

    def test_next_link_keeps_the_filters(self, api_client):
        response = api_client.get(URL, {"q": "milk", "page_size": 1})

        assert "q=milk" in response.data["next"]

    def test_page_size_is_capped(self, api_client):
        ProductFactory.create_batch(110)

        response = api_client.get(URL, {"page_size": 1000})

        assert len(response.data["results"]) == 100

    def test_page_beyond_the_end(self, api_client):
        assert api_client.get(URL, {"page": 99}).status_code == 404


class TestQueryCost:
    def test_number_of_queries_does_not_depend_on_the_result_size(
        self, api_client, dairy, django_assert_num_queries
    ):
        ProductFactory.create_batch(30, category=dairy)

        # 1. resolve ?category  2. COUNT for the pagination  3. the page itself
        with django_assert_num_queries(3):
            response = api_client.get(URL, {"category": dairy.pk, "page_size": 50})

        assert response.data["count"] == 33
