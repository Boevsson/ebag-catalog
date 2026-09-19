import pytest
from django.core.management import call_command

from catalog.models import Category, Product
from catalog.tests.factories import CategoryFactory

pytestmark = pytest.mark.django_db


class TestRebuildCategoryPaths:
    @pytest.fixture
    def drifted_tree(self):
        """Milk's stored path disagrees with its parent, as after a careless bulk update."""
        food = CategoryFactory(name="Food")
        dairy = CategoryFactory(name="Dairy", parent=food)
        milk = CategoryFactory(name="Milk", parent=dairy)
        Category.objects.filter(pk=milk.pk).update(ancestor_path="/123/456/")
        return {"food": food, "dairy": dairy, "milk": milk}

    def test_repairs_paths_from_the_parent_links(self, drifted_tree, capsys):
        call_command("rebuild_category_paths")

        milk = Category.objects.get(pk=drifted_tree["milk"].pk)
        assert milk.ancestor_path == drifted_tree["dairy"].path
        assert "1 path(s) repaired" in capsys.readouterr().out

    def test_dry_run_only_reports(self, drifted_tree, capsys):
        call_command("rebuild_category_paths", "--dry-run")

        milk = Category.objects.get(pk=drifted_tree["milk"].pk)
        assert milk.ancestor_path == "/123/456/"
        assert "1 path(s) would be repaired" in capsys.readouterr().out

    def test_consistent_tree_is_left_alone(self, capsys):
        CategoryFactory(parent=CategoryFactory())

        call_command("rebuild_category_paths")

        assert "0 path(s) repaired" in capsys.readouterr().out


class TestSeedCatalog:
    def test_creates_a_browsable_catalog(self):
        call_command("seed_catalog")

        assert Category.objects.filter(parent__isnull=True).exists()
        assert Category.objects.filter(parent__isnull=False).exists()
        assert Product.objects.count() >= 20

    def test_running_it_twice_changes_nothing(self):
        call_command("seed_catalog")
        before = (Category.objects.count(), Product.objects.count())

        call_command("seed_catalog")

        assert (Category.objects.count(), Product.objects.count()) == before
