"""
Use cases that change the category tree.

Products are plain rows and need no service. Categories are different: one
change can touch many rows (moving a category rewrites the path of every
descendant) and must keep several rules true at once:

* sibling names are unique,
* the tree has no cycles,
* `ancestor_path` always agrees with `parent`.

All of that lives here, so the API, the seed command and any future caller
(Celery task, import script) get the same behaviour.

Concurrency: structural changes are rare back-office operations, so they are
simply serialised - each one locks the category rows first (`_lock_tree`).
Reads take no lock. This is what makes the checks below trustworthy:
without it, two simultaneous moves (A under B, B under A) could each pass the
cycle check and together create a cycle, or a child created under a category
that is being moved could keep the old path.
"""

from collections import defaultdict
from typing import Any

from django.db import transaction
from django.db.models import Max, Q, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Concat, Length, Substr

from catalog.exceptions import (
    CategoryInUse,
    CategoryTreeTooDeep,
    DuplicateCategoryName,
    InvalidCategoryMove,
)
from catalog.models import Category
from catalog.models.category import PATH_MAX_LENGTH, ROOT_PATH


def create_category(*, name: str, parent: Category | None = None) -> Category:
    name = _clean_name(name)

    with transaction.atomic():
        _lock_tree()
        parent = _reread(parent)

        category = Category(name=name, parent=parent)
        ancestor_path = Category.ancestor_path_under(parent)
        _ensure_path_fits(len(ancestor_path))
        _ensure_name_is_free(name, ancestor_path)

        category.save()  # derives ancestor_path from parent
        return category


# `None` is a real value for `parent` ("top level"), so "not given" needs a marker of its own.
UNCHANGED: Any = object()


def update_category(
    category: Category,
    *,
    name: str = UNCHANGED,
    parent: Category | None = UNCHANGED,
) -> Category:
    """
    Rename and/or move `category`. An argument that is left out stays as it is.

    What "as it is" means is decided here, *after* the lock is taken and the
    row is read again - never from the object the caller holds, which was read
    before the lock and may be stale. Otherwise two simultaneous requests, one
    renaming and one moving the same category, would each write back the old
    value of the field they did not mean to touch: a lost update.

    The rules are checked against the final state (name and parent together).
    Checking a rename and a move separately would reject valid requests, e.g.
    moving "Organic" to a parent that already has an "Organic" while renaming
    it in the same call.
    """
    with transaction.atomic():
        _lock_tree()
        category.refresh_from_db()
        # One line per argument: what the row says now, unless the caller gave a value.
        name = category.name if name is UNCHANGED else _clean_name(name)
        parent = category.parent if parent is UNCHANGED else _reread(parent)

        old_prefix = category.path
        new_ancestor_path = Category.ancestor_path_under(parent)
        new_prefix = f"{new_ancestor_path}{category.pk}/"
        is_move = new_prefix != old_prefix

        if is_move:
            _ensure_not_moved_into_own_subtree(category, parent)
            _ensure_path_fits(len(new_ancestor_path))
            _ensure_descendant_paths_fit(category, growth=len(new_prefix) - len(old_prefix))
        _ensure_name_is_free(name, new_ancestor_path, ignore=category)

        category.name = name
        category.parent = parent
        category.ancestor_path = new_ancestor_path
        category.save()

        if is_move:
            _rewrite_descendant_paths(old_prefix, new_prefix)
        return category


def delete_category(category: Category) -> None:
    with transaction.atomic():
        _lock_tree()
        if category.children.exists():
            raise CategoryInUse(f'"{category.name}" still has sub-categories.')
        if category.products.exists():
            raise CategoryInUse(f'"{category.name}" still has products.')
        try:
            category.delete()
        except ProtectedError as error:
            # Safety net: should anything slip past the two checks above, it is
            # still reported as a domain error instead of a 500.
            raise CategoryInUse(f'"{category.name}" is still in use.') from error


def rebuild_category_paths(*, dry_run: bool = False) -> list[Category]:
    """
    Recompute every `ancestor_path` from the `parent` links (the source of
    truth) and return the categories whose stored path was wrong.

    The services above never let the two drift apart. This is the repair tool
    for whatever bypasses them: a bulk `QuerySet.update(parent=...)`, a data
    migration, or a manual fix in the database.
    """
    with transaction.atomic():
        _lock_tree()

        children: dict[int | None, list[Category]] = defaultdict(list)
        for category in Category.objects.order_by("pk"):
            children[category.parent_id].append(category)

        # Walk down from the top-level categories; each step knows the path
        # its children must have.
        repaired = []
        to_visit = [(ROOT_PATH, category) for category in children[None]]
        while to_visit:
            expected, category = to_visit.pop()
            if category.ancestor_path != expected:
                category.ancestor_path = expected
                repaired.append(category)
            to_visit.extend((category.path, child) for child in children[category.pk])

        if not dry_run:
            Category.objects.bulk_update(repaired, ["ancestor_path"])
        return repaired


# --- building blocks ------------------------------------------------------------


def _lock_tree() -> None:
    """
    Make this transaction the only one changing the tree until it commits.

    SELECT ... FOR UPDATE on all category rows: other structural writers queue
    up behind it, plain reads (the whole public API) are not affected. The
    table is small and these writes are rare, so the coarse lock costs nothing
    measurable and is far easier to reason about than per-subtree locking.
    Must be called inside `transaction.atomic()`.
    """
    # Evaluating the queryset is what actually sends the query.
    list(Category.objects.select_for_update().order_by("pk").values_list("pk", flat=True))


def _reread(category: Category | None) -> Category | None:
    """
    A category the caller passed in was read before we held the lock; it may
    have been moved since. `None` (the top level) has nothing to re-read.
    """
    if category is not None:
        category.refresh_from_db()
    return category


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("A category name cannot be blank.")
    return name


def _ensure_name_is_free(name: str, ancestor_path: str, ignore: Category | None = None) -> None:
    """
    Friendly version of the UNIQUE (ancestor_path, name) constraint: an error
    that names the field, instead of an IntegrityError.

    It has to find every name the index would refuse, and what the index
    considers equal depends on the column's collation. MariaDB's
    utf8mb4_unicode_ci ignores case and also treats "Straße" and "Strasse" as
    the same name. `name=` compares with the collation, exactly as the index
    does; `iexact` keeps "Dairy" and "DAIRY" a clash on a case-sensitive
    database too.
    """
    siblings = Category.objects.filter(
        Q(name=name) | Q(name__iexact=name), ancestor_path=ancestor_path
    )
    if ignore is not None:
        siblings = siblings.exclude(pk=ignore.pk)
    if siblings.exists():
        raise DuplicateCategoryName(f'A category named "{name}" already exists at this level.')


def _ensure_not_moved_into_own_subtree(category: Category, new_parent: Category | None) -> None:
    """
    Cycle check. With materialized paths it is a string comparison: the new
    parent is inside the moved subtree exactly when its path starts with the
    category's path (that also covers "parent == itself").
    """
    if new_parent is not None and new_parent.path.startswith(category.path):
        raise InvalidCategoryMove(
            "A category cannot be moved under itself or one of its own sub-categories."
        )


def _ensure_path_fits(length: int) -> None:
    if length > PATH_MAX_LENGTH:
        raise CategoryTreeTooDeep("The category tree cannot be nested this deep.")


def _ensure_descendant_paths_fit(category: Category, growth: int) -> None:
    """A move changes every descendant's path length by `growth`; check the longest one."""
    longest = Category.objects.descendants_of(category).aggregate(
        longest=Max(Length("ancestor_path"))
    )["longest"]
    if longest is not None:  # None: the category has no descendants
        _ensure_path_fits(longest + growth)


def _rewrite_descendant_paths(old_prefix: str, new_prefix: str) -> None:
    """
    Re-root a whole subtree with one UPDATE, whatever its size:

        ancestor_path = new_prefix + (ancestor_path without its old_prefix)

        "/1/5/"    ->  "/50/5/"       (old_prefix "/1/5/", new_prefix "/50/5/")
        "/1/5/23/" ->  "/50/5/23/"

    `Substr` is 1-based, so the part after the old prefix starts at
    len(old_prefix) + 1. The trailing "/" of `old_prefix` guarantees that
    "/1/5/" never matches "/1/50/...".
    """
    Category.objects.under_path(old_prefix).update(
        ancestor_path=Concat(Value(new_prefix), Substr("ancestor_path", len(old_prefix) + 1))
    )
