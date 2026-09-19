# eBag Catalog Service

A small REST service for the product catalog of an online shop: **products**, a
**category tree**, and a **search endpoint** that filters products by text/SKU,
price range and category (including all its sub-categories).

Python 3.13 · Django 5.2 LTS · Django REST Framework · MariaDB · JWT · Docker

## Run it

Requirements: Docker.

```bash
docker compose up --build -d web      # API + MariaDB, migrations run on start
docker compose exec web python manage.py seed_catalog      # demo data (optional)
docker compose exec web python manage.py createsuperuser   # a staff user, needed for writes
```

Then open **http://localhost:8000/api/docs** (Swagger UI). To call write
endpoints from there: `POST /api/v1/auth/token`, copy the `access` token and
paste it into **Authorize**.

To stop it: `docker compose down` keeps the data, `docker compose down --volumes`
deletes it. If a port is already taken on your machine:
`WEB_PORT=8080 DB_PORT=3310 docker compose up --build -d web`.

If you have `make`, `make help` lists shortcuts for all of this (`make up`,
`make seed`, `make test`, ...).

## API

| | |
|---|---|
| `GET/POST /api/v1/categories` · `GET/PUT/PATCH/DELETE /api/v1/categories/{id}` | Category CRUD. `?parent={id}` lists direct children, `?parent=root` the top level. |
| `GET/POST /api/v1/products` · `GET/PUT/PATCH/DELETE /api/v1/products/{id}` | Product CRUD. Images are uploaded as `multipart/form-data`; `"image": null` removes one. |
| `GET /api/v1/products/search` | Search and filter products (below). |
| `POST /api/v1/auth/token` · `POST /api/v1/auth/token/refresh` | Obtain / refresh a JWT. Rate limited. |
| `GET /healthz` · `GET /readyz` | Liveness (process only) and readiness (database reachable). |
| `GET /api/docs` · `GET /api/schema` | Swagger UI and the OpenAPI 3 schema. |

Reading is public. Writing needs `Authorization: Bearer <access token>` of a
**staff** user: shop customers have accounts too, and being logged in must not
be enough to edit the catalog.

Every error is JSON (`{"detail": ...}` or one list of messages per field),
including the ones Django would answer with an HTML page: an unknown URL, a
malformed request, an unhandled exception.

### Search

`GET /api/v1/products/search` - every parameter is optional, the given ones are
combined with AND, the result is paginated like every list.

| Parameter | Meaning |
|---|---|
| `q` | Words to find in the title **or** the SKU. Every word has to match, in any order; at most 10 words. Case-insensitive (Cyrillic included); `%` and `_` are ordinary characters. |
| `sku` | Exact SKU (case-insensitive). Meant for integrations. |
| `min_price`, `max_price` | Inclusive bounds. Either one can be omitted. |
| `category` | Category id. Products of **all its sub-categories** are included. |
| `ordering` | `title` (default), `-title`, `price`, `-price`, `created_at`, `-created_at`. Ties are broken by `id`, so pages are stable. |
| `page`, `page_size` | 20 per page by default, at most 100. |

```bash
# milk products under "Food" (any depth) between 1 and 3 EUR, cheapest first
curl "http://localhost:8000/api/v1/products/search?q=milk&category=1&min_price=1&max_price=3&ordering=price"
```

Invalid input is a `400` that names the parameter: a negative or malformed
price, `min_price > max_price`, an unknown `ordering`, a `category` that does not
exist (an unknown id is a client bug; answering `[]` would hide it). Empty or
whitespace-only parameters (`?q=&min_price=`) are treated as absent, the way
HTML forms send them.

## Design

```
config/                     settings (environment-driven), URLs, health checks
catalog/                    one bounded context: the catalog
  models/base.py            TimestampedModel: abstract base with created_at / updated_at
  models/category.py        Category + CategoryQuerySet (subtree_of, descendants_of)
  models/product.py         Product + ProductQuerySet (matching_terms, with_sku,
                            priced_between, in_category_tree)
  services/categories.py    use cases that change the tree: create / update / delete / rebuild
  search.py                 ProductSearchCriteria (value object) + search_products()
  validators.py             SKU normalization and format, image size
  exceptions.py             domain errors, free of HTTP
  api/                      serializers, viewsets, permissions, pagination, error mapping
  management/commands/      seed_catalog, rebuild_category_paths
  tests/
docs/adr/                   the decisions below, in more detail
```

The decisions that shape the code - each has a short record in [docs/adr](docs/adr):

1. **[Idiomatic Django, a service layer only where there are rules](docs/adr/0001-django-and-where-the-logic-lives.md).**
   Products are plain rows and go through DRF serializers. Categories have
   invariants that span rows, so every change to the tree goes through
   `catalog/services/categories.py`.
2. **[MariaDB, accessed only through the ORM](docs/adr/0002-database.md).**
   No raw SQL anywhere; the important rules (unique SKU, non-negative price,
   unique sibling names) are database constraints, not only Python checks.
3. **[Category tree = `parent` + a materialized path](docs/adr/0003-category-tree.md).**
   `parent` is the source of truth; `ancestor_path` (`/1/5/`) makes "everything
   under X" an indexed prefix match and gives a plain
   `UNIQUE (ancestor_path, name)` for sibling names, top level included.
4. **[Search: query string → criteria object → queryset filters](docs/adr/0004-search.md).**
   The criteria object validates itself and knows neither HTTP nor SQL; it is
   the seam where Elasticsearch could replace the database later.
5. **[JWT, public reads, staff-only writes](docs/adr/0005-authentication.md).**
6. **[Image storage is a configuration choice](docs/adr/0006-image-storage.md).**
   Local disk by default; `MEDIA_STORAGE=gcs` sends uploads to a Google Cloud
   Storage bucket (optionally behind a CDN) without touching the code.

### Things done deliberately

- **SKU** is normalized to upper-case on every write path and is **immutable**
  after creation (other systems refer to products by it).
- **Price** is a `Decimal` with a database `CHECK (price >= 0)`; validators alone
  do not protect against writers that skip the serializer.
- **Deleting a category** that still has sub-categories or products is a `409`,
  never a cascade.
- **Images** get random file names, are verified to be real images, are limited
  to 5 MB, and their files are removed when a product is deleted, its image is
  replaced, or the database refuses the row an upload belonged to (Django stores
  the file first). All of that goes through Django's storage API, never the
  file system directly, so the same code works on a disk and on a bucket.
- **Races** the validation cannot see (two requests creating the same SKU at
  once) are caught by database constraints and answered with `409`, not `500`.
  Changes to the category tree are serialized by a lock, and a `PATCH` (of a
  category or a product) only writes the fields it was given: a rename and a
  move of the same category arriving together both survive, and so do a price
  change and a title change of the same product.
- **Every list is paginated**, with a capped page size and a deterministic order.
- **URLs have no trailing slash**, unlike Django's default. That default answers
  `/products` with a redirect to `/products/`, and a redirected `POST` arrives
  as a `GET` without its body. Here every URL has one spelling and the other
  one is a plain `404` (`trailing_slash=False` on the router, `APPEND_SLASH = False`).

## Tests

Only Docker is needed:

```bash
docker compose run --rm --build test                 # the whole suite, against MariaDB   (= make test)
docker compose run --rm --build test pytest --cov    # + line and branch coverage; fails below 95%   (= make coverage)
```

`make lint` checks style with ruff; it runs on the host and needs [uv](https://docs.astral.sh/uv/).

On the host: `uv sync && docker compose up -d db && uv run pytest`
(`mysqlclient` compiles against the MariaDB client library; on macOS
`brew install mariadb-connector-c pkg-config`, and if the linker reports
`library 'ssl' not found`, prefix the sync with
`LDFLAGS="-L$(brew --prefix openssl@3)/lib"`).

The search tests, which the assignment asks for, are in three layers:

| File | What it proves | DB |
|---|---|---|
| `tests/test_search_criteria.py` | normalization and validation of the criteria object | no |
| `tests/test_product_search.py` | what matches what: text, SKU, price bounds, category subtrees (incl. the `/9007/` vs `/90070/` prefix trap), combinations, ordering, query count | yes |
| `tests/test_search_api.py` | query-string parsing, `400` responses, pagination, response shape, query count | yes |

The rest of the suite covers the category tree rules, the CRUD endpoints,
permissions/JWT, error mapping, configuration and the management commands
(272 tests, about 5 seconds).

**Coverage** is 100% of lines and branches of the application code (tests,
generated migrations and `wsgi.py` are excluded). `make coverage` fails below
95%: high enough to catch untested code, without forcing a test for every
future one-liner. The number only says the code *ran* under test; what gives it
meaning is that the tests assert behaviour, e.g. the race for one SKU really
ending in a `409` (`tests/test_exception_handler.py`).
`make coverage-html` writes a browsable report to `htmlcov/`.

## Configuration

Environment variables (a local `.env` is read if present; see `.env.example`):

| Variable | Default | |
|---|---|---|
| `DJANGO_SECRET_KEY` | required | |
| `DATABASE_URL` | required | `mysql://user:password@host:3306/name` |
| `DJANGO_DEBUG` | `false` | |
| `DJANGO_ALLOWED_HOSTS` | empty | comma separated. It applies to `/healthz` and `/readyz` too: a probe that calls an instance by its IP has to send an allowed `Host` header, or that address has to be listed |
| `JWT_SIGNING_KEY` | `DJANGO_SECRET_KEY` | set a dedicated key in production |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | `15` / `1` | |
| `AUTH_THROTTLE_RATE` | `10/min` | token endpoints |
| `NUM_PROXIES` | `0` | trusted reverse proxies in front of the service; `0` ignores `X-Forwarded-For` when the throttle identifies a client |
| `DJANGO_SERVE_MEDIA` | value of `DJANGO_DEBUG` | let Django serve uploaded images (dev/demo) |
| `DJANGO_MEDIA_ROOT` | `./media` | used when `MEDIA_STORAGE=local` |
| `MEDIA_STORAGE` | `local` | `local` (disk) or `gcs` (Google Cloud Storage); see [ADR 6](docs/adr/0006-image-storage.md) |
| `GCS_BUCKET_NAME` | required for `gcs` | |
| `GCS_CDN_URL` | empty | a CDN domain in front of the bucket; image URLs then point at it |
| `GCS_LOCATION` | empty | a folder inside the bucket |
| `DJANGO_BEHIND_TLS_PROXY` | `false` | HTTPS redirect, HSTS, trust `X-Forwarded-Proto`. The two health endpoints are never redirected |
| `DATABASE_CONN_MAX_AGE` | `60` | |
| `LOG_LEVEL` | `INFO` | logs go to stdout |

## Known limits and what I would do next

Kept out on purpose, to keep the scope small and each part well done:

- **Text search is `LIKE '%word%'`.** No relevance ranking, typo tolerance or
  Bulgarian stemming, and it scans the table: 3-4 ms for a page and 15-25 ms for
  the count with 60,000 products on a laptop - fine for a grocery catalog, not
  for millions of rows. Next step: index products in Elasticsearch and answer
  `search_products(criteria)` from it; the API contract would not change.
- **Images on local disk** (the default) work for one node. With several, set
  `MEDIA_STORAGE=gcs`. The Google driver is tested as far as possible without a
  Google account (it accepts the configured options and builds the right URLs;
  the application is proven storage-agnostic with an in-memory storage), but it
  has **not been run against a real bucket**. Existing files are not migrated
  by the switch: copy the `media/` folder into the bucket first.
- The 5 MB image limit is checked after the upload has been received, so the
  reverse proxy should cap the request size as well (nginx `client_max_body_size`).
- **Offset pagination** gets slower on very deep pages and can repeat a row if
  the data changes between two page requests. Acceptable for browsing a catalog.
- **Products are hard-deleted and a price is one number**: no soft delete, price
  history, VAT, currency or stock. These belong to the features (orders,
  pricing) that would need them.
- **Concurrent edits of one product: the last write wins.** Two back-office
  users saving the same field of a product (or sending a `PUT`) overwrite each
  other silently. The fix is
  optimistic locking: a version (or `updated_at` as an ETag) sent back with
  `If-Match`, and a `412` when it no longer matches.
- **Tokens cannot be revoked before they expire** (15 minutes). If that is
  needed: simplejwt's blacklist app plus refresh-token rotation.
- **Throttle counters are per process.** The image runs 3 workers, so a client
  really gets up to three times the configured login attempts. With several
  workers, point Django's cache at Redis.
- Not included: CORS headers (needed if a browser app on another origin calls
  the API), user management endpoints, metrics/error tracking (e.g. Sentry).
