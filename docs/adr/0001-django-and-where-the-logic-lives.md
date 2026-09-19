# 1. Django + DRF, and a service layer only where there are rules

## Context

The assignment allows FastAPI or Django. The service is CRUD over two related
models plus a search endpoint, and should be "small but carefully designed".

Domain-driven design offers many tactical patterns (repositories, aggregates,
domain entities separate from persistence models). In a Django code base most of
them duplicate what the framework already provides, and every extra layer is
code that has to be read, tested and maintained.

## Decision

- **Django 5.2 LTS + Django REST Framework.** Migrations, validation, the ORM,
  pagination, authentication hooks and OpenAPI generation are solved, boring
  problems there. 5.2 is the newest release that every dependency officially
  supports.
- **One bounded context, `catalog`,** with the vocabulary of the business in the
  code: *category tree, subtree, sibling, SKU, price range, criteria*.
- **Logic lives where the rule lives:**
  - A rule about **one row** sits on the model and, where possible, in the
    database: SKU normalization in `Product.save()`, `CHECK (price >= 0)`,
    `UNIQUE (ancestor_path, name)`.
  - A rule about **several rows** sits in a service. Only the category tree has
    such rules (no cycles, unique sibling names, paths of a whole subtree stay
    consistent), so `catalog/services/categories.py` is the only service.
  - **Read logic** is expressed as chainable `QuerySet` methods named after
    domain ideas (`in_category_tree`, `priced_between`), composed by
    `catalog/search.py`.
- **No repository layer.** The Django ORM already is the abstraction over the
  database (see ADR 2); wrapping it would add indirection without adding a
  capability.
- **Products have no service.** A pass-through `create_product()` that only calls
  `serializer.save()` would be ceremony. The day products get cross-row rules
  (e.g. price changes that must be audited), they get a service too.
- **Domain errors know nothing about HTTP** (`catalog/exceptions.py`). The API
  layer maps them: field-related ones become `400`s naming the field, "still in
  use" becomes `409`.

## Consequences

- Views and serializers stay thin; the tree rules are testable without HTTP and
  reusable from commands and future background tasks.
- There are two write paths (serializer for products, service for categories).
  The asymmetry is intentional and explained at the top of each module.
- `Category.save()` refuses a changed `parent` that did not come through the
  service, so the most likely accidental bypass fails loudly instead of
  corrupting paths silently.
