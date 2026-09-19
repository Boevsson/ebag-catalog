import pytest
from django.db import OperationalError
from django.test import Client


def test_liveness_does_not_need_the_database(client):
    # No `db` fixture: pytest-django would fail this test on any database access.
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_checks_the_database(client):
    assert client.get("/readyz").status_code == 200


def test_readiness_reports_an_unreachable_database(client, monkeypatch):
    """503 tells the load balancer to stop sending traffic to this instance."""

    class UnreachableDatabase:
        def ensure_connection(self):
            raise OperationalError("connection refused")

    # Replace the name inside the view's module; the real connection stays untouched.
    monkeypatch.setattr("config.health.connection", UnreachableDatabase())

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db
def test_openapi_schema_documents_the_search_parameters(client):
    response = client.get("/api/schema", {"format": "json"})

    assert response.status_code == 200
    search = response.json()["paths"]["/api/v1/products/search"]["get"]
    documented = {parameter["name"] for parameter in search["parameters"]}
    assert {"q", "sku", "min_price", "max_price", "category", "ordering", "page"} <= documented
    # The result is documented as the paginated envelope it really is, and the
    # error case is documented too.
    ok = search["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ok.endswith("/PaginatedProductList")
    assert "400" in search["responses"]


@pytest.mark.django_db
def test_no_api_url_ends_with_a_slash(client):
    """One spelling per URL. A hand-written path("something/") would show up here."""
    paths = client.get("/api/schema", {"format": "json"}).json()["paths"]

    assert paths
    assert [path for path in paths if path.endswith("/")] == []


class TestErrorsOutsideTheApiViewsAreJsonToo:
    """Django answers these itself, with HTML pages unless it is told otherwise."""

    def test_unknown_url(self, client):
        response = client.get("/api/v1/no-such-thing")

        assert response.status_code == 404
        assert response.json() == {"detail": "Not found."}

    def test_request_refused_by_django(self, client):
        response = client.get("/healthz", HTTP_HOST="not-an-allowed-host.example")

        assert response.status_code == 400
        assert response.json() == {"detail": "Bad request."}

    @pytest.mark.django_db
    def test_unhandled_exception(self, monkeypatch):
        def fails(criteria):
            raise RuntimeError("a bug")

        monkeypatch.setattr("catalog.api.views.search_products", fails)
        client = Client(raise_request_exception=False)  # behave like a real server

        response = client.get("/api/v1/products/search")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}


def test_absurdly_nested_json_is_a_bad_request_not_a_crash(api_client):
    """
    100,000 opening brackets exhaust Python's recursion limit inside DRF's JSON
    parser. The endpoint is public, so anyone can send this.
    """
    body = "[" * 100_000 + "]" * 100_000

    response = api_client.post("/api/v1/auth/token", body, content_type="application/json")

    assert response.status_code == 400
    assert response.json() == {"detail": "The JSON is nested too deeply."}


@pytest.mark.django_db
def test_the_spelling_with_a_slash_is_not_found_instead_of_redirected(client):
    """A redirect makes clients repeat a POST as a GET, without its body."""
    assert client.get("/api/v1/products").status_code == 200
    assert client.get("/api/v1/products/").status_code == 404
    assert client.post("/api/v1/auth/token/", {}).status_code == 404
