# 2. MariaDB, accessed only through the ORM

## Context

The assignment leaves the database open. eBag runs MariaDB, so the service
does too.

## Decision

- **MariaDB 11.4 (LTS)** with `utf8mb4`, so every Unicode character (Cyrillic
  product titles, emoji) can be stored.
- **All data access goes through the Django ORM.** There is no raw SQL in the
  application: queries are QuerySets, the schema comes from generated
  migrations, and even the bulk path rewrite of the category tree is written
  with ORM database functions (`Concat`, `Substr`).
- **The connection is configuration:** `DATABASE_URL`. Nothing about the
  database is hard-coded.
- **Rules are enforced by the database wherever possible,** not only in Python:
  unique SKU, `CHECK (price >= 0)`, `UNIQUE (ancestor_path, name)`, foreign keys
  that refuse to delete a category that is still in use. Validation in Python
  gives friendly errors; the constraints are what still holds when two requests
  race or someone writes to the table directly.
- **The collation is part of the design, so it is set explicitly.** On MariaDB
  Django compiles `icontains` and `iexact` to a plain `LIKE`. They ignore case
  only because the columns have a case-insensitive collation
  (`utf8mb4_unicode_ci`), so the text search and the uniqueness of sibling names
  depend on it. It is therefore not left to the server's default: the `db`
  service in `docker-compose.yml` and the test database in the settings both
  name it, and a production database has to be created with the same one.
  Two rules do *not* depend on it: SKUs are stored upper-case, so `ab-12` and
  `AB-12` are one product under any collation; and the friendly "this name is
  taken" check asks the database the same question the unique index asks
  (`name = ...`), so whatever the index would refuse is refused by the check
  first, with a proper error (under this collation "Straße" and "Strasse" are
  the same name).

## Consequences

- MariaDB has no partial or functional unique indexes. Unique sibling names are
  therefore modelled as a plain two-column constraint that also covers top-level
  categories (ADR 3).
- With a case-sensitive collation the text search would become case-sensitive.
  `db_collation` on the two columns would rule that out; it is left out because
  it would tie the migrations to MariaDB and break the quick SQLite run below.
- MariaDB cannot roll back schema changes (every `ALTER TABLE` commits at once),
  so once there is production data, migrations should stay small and
  single-purpose.
- Text search is `LIKE '%word%'` (ADR 4). MariaDB's FULLTEXT index is not used:
  Django has no built-in lookup for it, so it would mean raw SQL, and it matches
  whole words only.
- The tests need a real MariaDB, which Docker provides. The suite also happens
  to pass on SQLite - handy for a quick run without Docker - but SQLite is not a
  supported engine: its `LIKE` ignores case for ASCII only, so the Cyrillic
  search test is skipped there.
