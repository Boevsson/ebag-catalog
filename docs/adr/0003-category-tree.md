# 3. Category tree: `parent` plus a materialized ancestor path

## Context

The model must have `parent`, and search must find products "under a category",
meaning in that category or anywhere below it. With `parent` alone the database
only knows each node's direct parent; finding all descendants means walking the
tree. A shop asks that question on every category page and changes the tree
almost never.

Options considered:

| Option | Read "subtree of X" | Change the tree | Notes |
|---|---|---|---|
| `parent` only + recursive CTE | recursive query each time | one-row update | needs raw SQL in Django |
| Nested sets / MPTT | range query | renumbers large parts of the table | complex, write-heavy |
| django-treebeard `MP_Node` | prefix match | library API | replaces the `parent` FK the task asks for |
| **`parent` + materialized path** | **prefix match** | rewrite the subtree's paths | chosen |

## Decision

`parent` stays the **source of truth**. Next to it each category stores
`ancestor_path`: the ids of its ancestors, root first, each followed by `/`.

```
id  name     parent  ancestor_path   path (= ancestor_path + id + "/")
1   Food     -       /               /1/
5   Dairy    1       /1/             /1/5/
23  Milk     5       /1/5/           /1/5/23/
50  Drinks   -       /               /50/
```

- Descendants of Dairy: `ancestor_path LIKE '/1/5/%'` - one indexed prefix match,
  pure ORM (`Category.objects.descendants_of(dairy)`).
- The ORM lookup is `istartswith`, on purpose. On MariaDB `startswith` compiles
  to `LIKE BINARY`, and a binary comparison cannot use the index of a column
  with a case-insensitive collation: `EXPLAIN` shows a scan of the whole table.
  `istartswith` is the plain `LIKE` above - a range scan. Paths are digits and
  slashes, so ignoring case changes nothing. A test reads `EXPLAIN` and expects
  `range`, so this cannot silently regress.
- The trailing `/` is essential: without it `/1/5` would also match `/1/50/`.
  Tests pin this down with ids 9007 and 90070.
- Cycle check is a string comparison: X may not move under P if
  `P.path.startswith(X.path)`.

**Why the stored path excludes the row's own id** (it is the "directory", `path`
is the "full file name"):

1. It is known *before* the INSERT, so creating a category is one statement
   instead of insert-then-update.
2. All siblings share the same `ancestor_path`, so
   `UNIQUE (ancestor_path, name)` means "no two siblings with the same name".
   A constraint on the nullable `parent` column cannot cover top-level
   categories (NULLs are distinct), and the workarounds - partial or functional
   indexes - do not exist on MariaDB.

**Keeping the copy consistent.** All structural writes go through
`catalog/services/categories.py`:

- `update_category` validates rename + move against the *final* state, then
  re-roots the whole subtree with a single `UPDATE … SET ancestor_path =
  CONCAT(new_prefix, SUBSTR(ancestor_path, len(old_prefix) + 1))`.
- An argument that is left out (a `PATCH` with only `name`) is filled in by the
  service *after* it holds the lock, from the row as it is then - never from the
  copy the view read before. Otherwise a rename and a move of the same category
  arriving together would each write back the other one's old value (a lost
  update); tests replay exactly that.
- Writers are serialized with `SELECT … FOR UPDATE` on the category rows.
  Otherwise two concurrent moves (A under B, B under A) could both pass the
  cycle check. Reads take no lock. The table is small and these writes are rare
  back-office actions, so a coarse lock is cheap and easy to reason about.
- `Category.save()` derives the path on insert and refuses an update whose
  `parent` and `ancestor_path` disagree.
- `manage.py rebuild_category_paths [--dry-run]` recomputes all paths from the
  `parent` links, for anything that bypassed the service (bulk update, manual SQL).

## Consequences

- Reads are trivial and fast; writes are more code than a bare `parent` update.
  That is the right way round for a catalog. If the tree were edited constantly,
  the recursive CTE would be the better trade.
- Writers wait for the lock without a timeout. With a handful of synchronous
  workers, several queued writers could occupy all of them and delay reads for
  as long as the first one takes. Tree changes are staff-only, rare and take
  milliseconds, so this is accepted; if it mattered:
  `select_for_update(nowait=True)` and a `409`.
- `rebuild_category_paths` walks down from the top level along the `parent`
  links, so it repairs wrong paths but cannot see a cycle written directly into
  the database (the service makes cycles impossible).
- `ancestor_path` is limited to 255 characters (25 levels even with 9-digit
  ids); deeper nesting is rejected with a clear error.
- Moving a subtree does not update its descendants' `updated_at`: their own data
  did not change.
