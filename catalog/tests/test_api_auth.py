"""Who may do what: anyone reads, only staff writes, identity comes from a JWT."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/token/refresh"
CATEGORIES_URL = "/api/v1/categories"
PRODUCTS_URL = "/api/v1/products"


@pytest.fixture
def customer_client() -> APIClient:
    """Logged in, but not staff - like a shopper with an account."""
    user = get_user_model().objects.create_user("customer", password="pw")
    client = APIClient()
    client.force_authenticate(user)
    return client


def write_requests(client: APIClient):
    category = CategoryFactory()
    product = ProductFactory()
    return [
        client.post(CATEGORIES_URL, {"name": "New"}, format="json"),
        client.patch(f"{CATEGORIES_URL}/{category.pk}", {"name": "Renamed"}, format="json"),
        client.delete(f"{CATEGORIES_URL}/{category.pk}"),
        client.post(PRODUCTS_URL, {}, format="json"),
        client.patch(f"{PRODUCTS_URL}/{product.pk}", {"title": "Renamed"}, format="json"),
        client.delete(f"{PRODUCTS_URL}/{product.pk}"),
    ]


class TestPermissions:
    def test_anonymous_users_cannot_write(self, api_client):
        assert {response.status_code for response in write_requests(api_client)} == {401}

    def test_customers_cannot_write(self, customer_client):
        assert {response.status_code for response in write_requests(customer_client)} == {403}

    def test_everybody_can_read(self, api_client, customer_client):
        for client in (api_client, customer_client):
            assert client.get(CATEGORIES_URL).status_code == 200
            assert client.get(PRODUCTS_URL).status_code == 200


class TestJwt:
    @pytest.fixture(autouse=True)
    def staff_user(self):
        return get_user_model().objects.create_user("editor", password="s3cret!", is_staff=True)

    def test_token_lets_staff_write(self, api_client):
        tokens = api_client.post(TOKEN_URL, {"username": "editor", "password": "s3cret!"}).data
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = api_client.post(CATEGORIES_URL, {"name": "Food"}, format="json")

        assert response.status_code == 201

    def test_wrong_password(self, api_client):
        response = api_client.post(TOKEN_URL, {"username": "editor", "password": "nope"})

        assert response.status_code == 401

    def test_garbage_token(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")

        # Even a public endpoint rejects a token that was sent but is not valid.
        assert api_client.get(CATEGORIES_URL).status_code == 401

    def test_password_guessing_is_rate_limited(self, api_client):
        guesses = [
            api_client.post(TOKEN_URL, {"username": "editor", "password": f"guess-{n}"})
            for n in range(11)  # the limit is 10 per minute
        ]

        assert [response.status_code for response in guesses] == [401] * 10 + [429]

    def test_a_forged_forwarded_for_header_is_not_a_new_client(self, api_client):
        """
        Left to its default, DRF identifies a client by whatever X-Forwarded-For
        says: a different value per request would be an unlimited allowance.
        """
        guesses = [
            api_client.post(
                TOKEN_URL,
                {"username": "editor", "password": f"guess-{n}"},
                HTTP_X_FORWARDED_FOR=f"10.0.0.{n}",
            )
            for n in range(11)
        ]

        assert [response.status_code for response in guesses] == [401] * 10 + [429]

    def test_refresh_gives_a_new_access_token(self, api_client):
        tokens = api_client.post(TOKEN_URL, {"username": "editor", "password": "s3cret!"}).data

        response = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]})

        assert response.status_code == 200
        assert "access" in response.data
