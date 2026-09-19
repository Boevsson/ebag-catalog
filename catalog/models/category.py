"""
The category tree.

`parent` is the source of truth for the structure. `ancestor_path` is a
denormalised copy of it (a "materialized path") that turns the question the
catalog asks all the time - "which categories are under X?" - into a plain,
index-friendly prefix match instead of a recursive walk.

Example::

    id  name     parent  ancestor_path   path (derived)
    1   Food     -       /               /1/
    5   Dairy    1       /1/             /1/5/
    23  Milk     5       /1/5/           /1/5/23/
    50  Drinks   -       /               /50/

    everything under Dairy  ==  ancestor_path starts with "/1/5/"

Think of a file system: `ancestor_path` is the *directory* a category lives
in and `path` is its full path. Two details follow from that picture:

* Siblings share a directory, so `UNIQUE (ancestor_path, name)` is exactly
  "no two siblings with the same name". Unlike a constraint on the nullable
  `parent` column it also covers top-level categories (NULLs never collide
  in a unique index, and MariaDB has no partial indexes to work around that).
* The stored value does not contain the row's own id, so it is known before
  the INSERT and a category is created with a single statement.

Every id is followed by "/" so that "/1/5/" is never a prefix of "/1/50/".

Changing the structure means rewriting the paths of a whole subtree, which is
why it has to go through `catalog.services.categories`.
"""

from django.db import models
from django.db.models import Q

from catalog.models.base import TimestampedModel

ROOT_PATH = "/"
# Long enough for any realistic tree: even with 9-digit ids this is 25 levels.
PATH_MAX_LENGTH = 255


def _path_starts_with(prefix: str) -> Q:
    """
    The prefix match on `ancestor_path`, in the one form that uses its index.

    `istartswith`, not `startswith`: on MariaDB Django compiles `startswith` to
    `LIKE BINARY 'prefix%'`, and a binary comparison cannot use the index of a
    column with a case-insensitive collation - EXPLAIN shows a full scan.
    `istartswith` is a plain `LIKE 'prefix%'`: an index range scan. Paths
    contain only digits and "/", so ignoring case changes nothing.
    A test runs EXPLAIN to keep this true.
    """
    return Q(ancestor_path__istartswith=prefix)


class CategoryQuerySet(models.QuerySet):
    def under_path(self, prefix: str) -> "CategoryQuerySet":
        """Categories whose ancestors start with `prefix`, e.g. "/1/5/"."""
        return self.filter(_path_starts_with(prefix))

    def descendants_of(self, category: "Category") -> "CategoryQuerySet":
        """Everything below `category`, at any depth, excluding `category` itself."""
        return self.under_path(category.path)

    def subtree_of(self, category: "Category") -> "CategoryQuerySet":
        """`category` plus all of its descendants."""
        return self.filter(Q(pk=category.pk) | _path_starts_with(category.path))


class Category(TimestampedModel):
    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        # Deleting a category never silently deletes (or orphans) what is in it.
        on_delete=models.PROTECT,
        related_name="children",
    )
    ancestor_path = models.CharField(max_length=PATH_MAX_LENGTH, default=ROOT_PATH, editable=False)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name", "id"]
        constraints = [
            # Doubles as the index behind the `ancestor_path LIKE 'prefix%'` lookups.
            models.UniqueConstraint(
                fields=["ancestor_path", "name"],
                name="category_unique_name_among_siblings",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        expected = self.ancestor_path_under(self.parent)
        if self._state.adding:
            # Keeps plain `Category.objects.create(...)` (tests, fixtures, shell) correct.
            self.ancestor_path = expected
        elif self.ancestor_path != expected:
            # `parent` was changed without moving the subtree along with it.
            raise RuntimeError(
                "A category's parent must be changed with "
                "catalog.services.categories.update_category(), which also "
                "rewrites the paths of its descendants."
            )
        super().save(*args, **kwargs)

    @property
    def path(self) -> str:
        """Full path including this category; the prefix shared by all its descendants."""
        if self.pk is None:
            raise ValueError("A category has no path until it has been saved.")
        return f"{self.ancestor_path}{self.pk}/"

    @property
    def depth(self) -> int:
        """0 for top-level categories, 1 for their children, ..."""
        return self.ancestor_path.count("/") - 1

    @staticmethod
    def ancestor_path_under(parent: "Category | None") -> str:
        """The `ancestor_path` of a category that is (or would be) a child of `parent`."""
        return parent.path if parent is not None else ROOT_PATH
