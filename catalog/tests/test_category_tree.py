"""
Rules of the category tree.

Vocabulary used below:
  ancestor_path  ids of all ancestors, root first:  "/"  or  "/1/5/"   (stored)
  path           ancestor_path + own id:            "/1/" or "/1/5/23/" (derived)
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext

from catalog.exceptions import (
    CategoryInUse,
    CategoryTreeTooDeep,
    DuplicateCategoryName,
    InvalidCategoryMove,
)
from catalog.models import Category, Product
from catalog.models.category import PATH_MAX_LENGTH, ROOT_PATH
from catalog.services.categories import (
    create_category,
    delete_category,
    rebuild_category_paths,
    update_category,
)
from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db


def reload(category: Category) -> Category:
    return Category.objects.get(pk=category.pk)


class TestCreateCategory:
    def test_root_category_sits_directly_under_the_root_path(self):
        food = create_category(name="Food")

        assert food.parent is None
        assert food.ancestor_path == ROOT_PATH
        assert food.path == f"/{food.pk}/"
        assert food.depth == 0

    def test_child_path_extends_the_parent_path(self):
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)
        milk = create_category(name="Milk", parent=dairy)

        assert milk.ancestor_path == f"/{food.pk}/{dairy.pk}/"
        assert milk.path == f"/{food.pk}/{dairy.pk}/{milk.pk}/"
        assert milk.depth == 2

    def test_name_is_trimmed(self):
        assert create_category(name="  Food  ").name == "Food"

    def test_blank_name_is_rejected(self):
        with pytest.raises(ValueError, match="name"):
            create_category(name="   ")

    def test_duplicate_name_among_siblings_is_rejected(self):
        food = create_category(name="Food")
        create_category(name="Dairy", parent=food)

        with pytest.raises(DuplicateCategoryName):
            create_category(name="Dairy", parent=food)

    def test_duplicate_check_ignores_case(self):
        create_category(name="Food")

        with pytest.raises(DuplicateCategoryName):
            create_category(name="FOOD")

    @pytest.mark.skipif(
        connection.vendor != "mysql", reason="about MariaDB's utf8mb4_unicode_ci collation"
    )
    def test_names_that_are_equal_for_the_unique_index_are_duplicates(self):
        """
        Under utf8mb4_unicode_ci "Straße" equals "Strasse", so the index refuses
        the second one. The friendly check has to agree with the index;
        otherwise the client gets a 409 "please retry" that can never succeed.
        """
        food = create_category(name="Food")
        create_category(name="Strasse", parent=food)

        with pytest.raises(DuplicateCategoryName):
            create_category(name="Straße", parent=food)

    def test_duplicate_name_among_root_categories_is_rejected(self):
        """Roots have no parent row, the rule must hold for them as well."""
        create_category(name="Food")

        with pytest.raises(DuplicateCategoryName):
            create_category(name="Food")

    def test_same_name_is_fine_under_different_parents(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")

        create_category(name="Organic", parent=food)
        create_category(name="Organic", parent=drinks)  # does not raise

    def test_database_enforces_sibling_uniqueness_when_the_service_is_bypassed(self):
        """The constraint is the safety net for races the service check cannot see."""
        Category.objects.create(name="Food")

        with pytest.raises(IntegrityError), transaction.atomic():
            Category.objects.create(name="Food")

    def test_plain_model_creation_still_derives_the_path(self):
        """Factories, fixtures and the shell do not go through the service."""
        food = Category.objects.create(name="Food")
        dairy = Category.objects.create(name="Dairy", parent=food)

        assert dairy.ancestor_path == food.path

    def test_tree_depth_is_limited_by_the_path_column(self):
        parent = create_category(name="Root")
        # Pretend the parent is already as deep as the column allows.
        Category.objects.filter(pk=parent.pk).update(
            ancestor_path="/" + "9" * (PATH_MAX_LENGTH - 2) + "/"
        )
        parent = reload(parent)

        with pytest.raises(CategoryTreeTooDeep):
            create_category(name="One level too many", parent=parent)


class TestUpdateCategory:
    def test_rename_keeps_the_position(self):
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)

        update_category(dairy, name="Dairy & Eggs", parent=food)

        dairy = reload(dairy)
        assert dairy.name == "Dairy & Eggs"
        assert dairy.ancestor_path == food.path

    def test_new_name_is_trimmed(self):
        food = create_category(name="Food")

        update_category(food, name="  Groceries  ")

        assert reload(food).name == "Groceries"

    def test_rename_to_a_sibling_name_is_rejected(self):
        food = create_category(name="Food")
        create_category(name="Dairy", parent=food)
        bakery = create_category(name="Bakery", parent=food)

        with pytest.raises(DuplicateCategoryName):
            update_category(bakery, name="Dairy", parent=food)

    def test_an_argument_that_is_left_out_stays_as_it_is(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)

        update_category(dairy, name="Dairy & Eggs")
        assert reload(dairy).parent == food

        update_category(dairy, parent=drinks)
        assert reload(dairy).name == "Dairy & Eggs"
        assert reload(dairy).parent == drinks

    def test_none_is_a_parent_too(self):
        """`parent=None` means "top level"; only leaving the argument out means "unchanged"."""
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)

        update_category(dairy, parent=None)

        assert reload(dairy).parent is None

    def test_saving_without_changes_is_not_a_duplicate_of_itself(self):
        food = create_category(name="Food")

        update_category(food, name="Food", parent=None)  # does not raise

    def test_move_rewrites_the_paths_of_the_whole_subtree(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)
        milk = create_category(name="Milk", parent=dairy)
        oat_milk = create_category(name="Oat milk", parent=milk)

        update_category(dairy, name="Dairy", parent=drinks)

        dairy, milk, oat_milk = reload(dairy), reload(milk), reload(oat_milk)
        assert dairy.parent == drinks
        assert dairy.path == f"/{drinks.pk}/{dairy.pk}/"
        assert milk.path == f"/{drinks.pk}/{dairy.pk}/{milk.pk}/"
        assert oat_milk.path == f"/{drinks.pk}/{dairy.pk}/{milk.pk}/{oat_milk.pk}/"

    def test_move_to_the_top_level(self):
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)
        milk = create_category(name="Milk", parent=dairy)

        update_category(dairy, name="Dairy", parent=None)

        assert reload(dairy).ancestor_path == ROOT_PATH
        assert reload(milk).path == f"/{dairy.pk}/{milk.pk}/"

    def test_move_leaves_categories_with_a_similar_id_prefix_alone(self):
        """
        "/9007/" is a string prefix of "/90070/" only if the trailing slash is
        forgotten. Moving 9007 must never drag 90070's subtree along.
        """
        short = CategoryFactory(pk=9007)
        long = CategoryFactory(pk=90070)
        under_long = CategoryFactory(parent=long)
        target = CategoryFactory()

        update_category(short, name=short.name, parent=target)

        assert reload(long).ancestor_path == ROOT_PATH
        assert reload(under_long).ancestor_path == "/90070/"

    def test_category_cannot_become_its_own_parent(self):
        food = create_category(name="Food")

        with pytest.raises(InvalidCategoryMove):
            update_category(food, name="Food", parent=food)

    def test_category_cannot_move_under_its_own_descendant(self):
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)
        milk = create_category(name="Milk", parent=dairy)

        with pytest.raises(InvalidCategoryMove):
            update_category(food, name="Food", parent=milk)

        assert reload(food).parent is None  # nothing was changed

    def test_move_is_rejected_when_the_destination_already_has_that_name(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        create_category(name="Organic", parent=food)
        organic_drinks = create_category(name="Organic", parent=drinks)

        with pytest.raises(DuplicateCategoryName):
            update_category(organic_drinks, name="Organic", parent=food)

    def test_move_and_rename_are_validated_against_the_final_state(self):
        """'Organic' exists in both places; moving *and* renaming in one step is fine."""
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        create_category(name="Organic", parent=food)
        organic_drinks = create_category(name="Organic", parent=drinks)

        update_category(organic_drinks, name="Organic drinks", parent=food)

        assert reload(organic_drinks).parent == food

    def test_move_is_rejected_when_a_descendant_path_would_not_fit(self):
        deep_parent = create_category(name="Deep")
        # Make `deep_parent.path` two characters short of the limit: "Branch"
        # itself still fits underneath, its grandchild no longer does.
        ancestor_path_length = (PATH_MAX_LENGTH - 2) - len(f"{deep_parent.pk}/")
        Category.objects.filter(pk=deep_parent.pk).update(
            ancestor_path="/" + "9" * (ancestor_path_length - 2) + "/"
        )
        branch = create_category(name="Branch")
        leaf = create_category(name="Leaf", parent=branch)
        create_category(name="Leaf of leaf", parent=leaf)

        with pytest.raises(CategoryTreeTooDeep):
            update_category(branch, name="Branch", parent=reload(deep_parent))

    def test_changing_the_parent_with_a_plain_save_is_refused(self):
        """A plain save() would leave the descendants' paths stale."""
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)

        dairy.parent = drinks
        with pytest.raises(RuntimeError, match="update_category"):
            dairy.save()


class TestStaleCallers:
    """
    The object a caller passes in was read *before* the service took its lock,
    so another request may have changed that row in between. These tests play
    the other request by hand; the service must work with what is in the
    database now, not with what the caller remembers.
    """

    def test_a_rename_does_not_undo_a_move_it_has_not_seen(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)
        read_earlier = reload(dairy)

        update_category(dairy, parent=drinks)  # somebody else moves it
        update_category(read_earlier, name="Dairy & Eggs")  # this caller only renames

        dairy = reload(dairy)
        assert dairy.name == "Dairy & Eggs"
        assert dairy.parent == drinks

    def test_a_move_does_not_undo_a_rename_it_has_not_seen(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)
        read_earlier = reload(dairy)

        update_category(dairy, name="Dairy & Eggs")
        update_category(read_earlier, parent=drinks)

        dairy = reload(dairy)
        assert dairy.name == "Dairy & Eggs"
        assert dairy.parent == drinks

    def test_a_child_is_created_under_the_current_path_of_its_parent(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        juice = create_category(name="Juice", parent=drinks)
        read_earlier = reload(juice)

        update_category(drinks, parent=food)  # moves Juice along with it
        apple = create_category(name="Apple", parent=read_earlier)

        assert apple.ancestor_path == f"/{food.pk}/{drinks.pk}/{juice.pk}/"

    def test_a_category_is_moved_under_the_current_path_of_its_new_parent(self):
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        juice = create_category(name="Juice", parent=drinks)
        smoothies = create_category(name="Smoothies")
        read_earlier = reload(juice)

        update_category(drinks, parent=food)
        update_category(smoothies, parent=read_earlier)

        assert reload(smoothies).ancestor_path == f"/{food.pk}/{drinks.pk}/{juice.pk}/"


class TestTreeLockAndTransaction:
    @pytest.mark.skipif(
        not connection.features.has_select_for_update, reason="SQLite has no row locks"
    )
    def test_every_use_case_locks_the_tree_before_it_reads_it(self):
        """A check made before the lock is a check made on data that may already be gone."""
        food = create_category(name="Food")
        use_cases = {
            "create": lambda: create_category(name="Drinks"),
            "update": lambda: update_category(food, name="Groceries"),
            "rebuild": lambda: rebuild_category_paths(),
            "delete": lambda: delete_category(food),
        }

        for name, use_case in use_cases.items():
            with CaptureQueriesContext(connection) as queries:
                use_case()

            on_categories = [q["sql"] for q in queries if "catalog_category" in q["sql"]]
            assert "FOR UPDATE" in on_categories[0], name

    def test_a_move_that_fails_half_way_changes_nothing(self, monkeypatch):
        """The category row and its descendants' paths are two statements: both or neither."""
        food = create_category(name="Food")
        drinks = create_category(name="Drinks")
        dairy = create_category(name="Dairy", parent=food)
        create_category(name="Milk", parent=dairy)

        def fails(*args):
            raise RuntimeError("the UPDATE of the descendants failed")

        monkeypatch.setattr("catalog.services.categories._rewrite_descendant_paths", fails)

        with pytest.raises(RuntimeError):
            update_category(dairy, parent=drinks)

        assert reload(dairy).parent == food
        assert reload(dairy).ancestor_path == food.path


class TestDeleteCategory:
    def test_leaf_category_can_be_deleted(self):
        food = create_category(name="Food")

        delete_category(food)

        assert not Category.objects.filter(pk=food.pk).exists()

    def test_category_with_children_cannot_be_deleted(self):
        food = create_category(name="Food")
        create_category(name="Dairy", parent=food)

        with pytest.raises(CategoryInUse):
            delete_category(food)

    def test_category_with_products_cannot_be_deleted(self):
        food = create_category(name="Food")
        ProductFactory(category=food)

        with pytest.raises(CategoryInUse):
            delete_category(food)

    def test_protection_by_the_orm_is_reported_as_a_domain_error_too(self, monkeypatch):
        """
        Safety net: should something ever slip past the two checks in
        delete_category, Django's PROTECT still stops the delete - and callers
        keep getting CategoryInUse instead of an ORM exception.
        """
        food = create_category(name="Food")

        def protected(self, *args, **kwargs):
            raise ProtectedError("still referenced", set())

        monkeypatch.setattr(Category, "delete", protected)

        with pytest.raises(CategoryInUse):
            delete_category(food)


class TestCategoryModel:
    def test_is_displayed_by_its_name(self):
        assert str(Category(name="Dairy")) == "Dairy"

    def test_has_no_path_before_it_is_saved(self):
        """The path contains the id, which only exists after the INSERT."""
        with pytest.raises(ValueError, match="saved"):
            _ = Category(name="Dairy").path


class TestCategoryQuerySet:
    @pytest.fixture
    def tree(self):
        food = create_category(name="Food")
        dairy = create_category(name="Dairy", parent=food)
        milk = create_category(name="Milk", parent=dairy)
        bakery = create_category(name="Bakery", parent=food)
        drinks = create_category(name="Drinks")
        return {"food": food, "dairy": dairy, "milk": milk, "bakery": bakery, "drinks": drinks}

    def test_descendants_of_excludes_the_category_itself(self, tree):
        assert set(Category.objects.descendants_of(tree["food"])) == {
            tree["dairy"],
            tree["milk"],
            tree["bakery"],
        }

    def test_subtree_of_includes_the_category_itself(self, tree):
        assert set(Category.objects.subtree_of(tree["dairy"])) == {tree["dairy"], tree["milk"]}

    def test_subtree_of_a_leaf_is_just_the_leaf(self, tree):
        assert list(Category.objects.subtree_of(tree["milk"])) == [tree["milk"]]

    def test_prefix_lookups_are_a_plain_like(self, tree):
        """
        On MariaDB `startswith` becomes LIKE BINARY, which cannot use the index
        of a case-insensitive column; `istartswith` is a plain LIKE 'prefix%'.
        """
        lookups = [
            Category.objects.descendants_of(tree["food"]),
            Category.objects.subtree_of(tree["food"]),
            Category.objects.under_path(tree["food"].path),
            Product.objects.in_category_tree(tree["food"]),
        ]

        for queryset in lookups:
            assert "LIKE BINARY" not in str(queryset.query)

    @pytest.mark.skipif(connection.vendor != "mysql", reason="reads MariaDB's EXPLAIN output")
    def test_descendants_are_found_with_an_index_range_scan(self, tree):
        """The reason the path is stored at all: "everything under X" must not scan the table."""
        # Enough rows elsewhere in the tree for the optimizer to care. bulk_create
        # skips save(), so the paths are written by hand; they only have to be
        # *different* from Dairy's.
        elsewhere = [
            Category(name=f"elsewhere {n}", ancestor_path=f"/{900_000 + n % 50}/")
            for n in range(2000)
        ]
        under_dairy = [
            Category(name=f"cheese {n}", parent=tree["dairy"], ancestor_path=tree["dairy"].path)
            for n in range(20)
        ]
        Category.objects.bulk_create(elsewhere + under_dairy)

        plan = Category.objects.descendants_of(tree["dairy"]).explain()

        assert " range " in plan
        assert "category_unique_name_among_siblings" in plan
