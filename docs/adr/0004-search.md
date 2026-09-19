# 4. Search: query string → criteria object → queryset filters

## Context

A client must be able to find products by name/SKU, within a price range and
under a category, in any combination. The endpoint will be the most used and the
most changed part of the service (new filters arrive all the time).

## Decision

Three small pieces, each with one job:

```
?q=milk&max_price=3&category=5          HTTP query string
      │  ProductSearchQuerySerializer    parse + type-check, one 400 listing every bad parameter
      ▼
ProductSearchCriteria                    frozen dataclass: normalizes and validates itself
      │  search_products(criteria)       composes the filters, adds ordering
      ▼
ProductQuerySet                          matching_terms · with_sku · priced_between · in_category_tree
```

- **`ProductSearchCriteria` is the seam.** It knows neither HTTP nor SQL. Rules
  about the question itself (`min_price <= max_price`, no negative prices, blank
  text means "no text filter") live there once, for every caller - the API
  today, a Celery task or an export command tomorrow. The serializer only
  translates its `InvalidSearchCriteria` into a field-level `400`. Limits that
  protect the public endpoint rather than define the question (at most 10
  words, 100 characters, the page size) stay in the HTTP layer.
- **Filters are chainable `QuerySet` methods** named after domain ideas. Each is
  a few lines, testable alone, and adding a filter is one method plus one line
  in `search_products`.
- **No `django-filter`.** It would work; for the core of the assignment I
  preferred explicit code and a criteria object that is not tied to HTTP.
- **A dedicated `/products/search` endpoint**, as the assignment asks; the plain
  list stays a plain list.

Semantics, chosen deliberately:

- `q` is split into words and **every word must appear in the title or the SKU**
  (`icontains`), so word order does not matter and more words narrow the result.
  Django escapes `%` and `_`, so user input cannot act as a wildcard.
- `sku` is an exact, case-insensitive match for integrations.
- Price bounds are **inclusive**; `category` includes **all descendants**.
- An unknown `category` id is a `400`, not an empty list: it is a client bug and
  hiding it helps nobody.
- Ordering comes from a whitelist (`ProductOrdering`) and always ends with `id`,
  otherwise rows with equal prices could swap places between two pages.
- `select_related("category")` keeps the endpoint at a constant three queries
  (resolve category, count, page), asserted by tests.

## Consequences

- Checked with `EXPLAIN` on MariaDB 11.4 with 60,000 products in 2,220
  categories: the SKU (`const`), price (`range`) and category filters (a range
  on the path index, then the `category_id` index) are index lookups and answer
  in about 1-5 ms per page, a leaf category as fast as a top-level one. The text
  search cannot use an index: a page takes about 3-4 ms (it stops after 20
  matches), the count 15-25 ms (it has to look at every row).
- The text search has no relevance ranking, typo tolerance or stemming. That is
  acceptable for a grocery assortment and is the price of staying inside the
  ORM (ADR 2).
- When it stops being acceptable, products get indexed in Elasticsearch and
  `search_products(criteria)` is answered from there. The criteria object, the
  API contract and the API tests stay as they are. What changes with it is the
  hand-over to the view: today that is a lazy QuerySet which the view
  paginates; Elasticsearch returns one page of ids and a total, so pagination
  moves into `search_products`.
