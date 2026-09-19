from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Category, Product
from catalog.services.categories import create_category

# {category name: sub-tree}; the products of each category are in PRODUCTS below.
TREE = {
    "Food": {
        "Dairy & Eggs": {"Milk": {}, "Cheese": {}, "Yoghurt": {}, "Eggs": {}},
        "Bakery": {"Bread": {}, "Pastry": {}},
        "Fruit & Vegetables": {"Fruit": {}, "Vegetables": {}},
    },
    "Drinks": {
        "Water": {},
        "Juice": {},
        "Coffee & Tea": {},
    },
    "Household": {
        "Cleaning": {},
    },
}

# (category, SKU, title, price in EUR). A few Bulgarian titles show that the
# search is case-insensitive for Cyrillic as well.
PRODUCTS = [
    ("Milk", "MILK-FRESH-1L", "Fresh Milk 3.6% 1L", "1.49"),
    ("Milk", "MILK-UHT-1L", "UHT Milk 1.5% 1L", "1.19"),
    ("Milk", "MILK-OAT-1L", "Oat Milk Barista 1L", "2.79"),
    ("Milk", "MLYAKO-PRYASNO-1L", "Прясно мляко 3% 1л", "1.39"),
    ("Cheese", "CHEESE-WHITE-400", "Bulgarian White Cheese 400g", "4.59"),
    ("Cheese", "CHEESE-YELLOW-300", "Kashkaval Yellow Cheese 300g", "5.29"),
    ("Cheese", "SIRENE-KRAVE-800", "Краве сирене 800г", "8.49"),
    ("Yoghurt", "YOGHURT-36-400", "Bulgarian Yoghurt 3.6% 400g", "0.79"),
    ("Yoghurt", "KISELO-MLYAKO-2", "Кисело мляко 2% 400г", "0.69"),
    ("Eggs", "EGGS-M-10", "Free Range Eggs M x10", "2.89"),
    ("Dairy & Eggs", "BUTTER-82-250", "Butter 82% 250g", "3.49"),
    ("Bread", "BREAD-WHITE-650", "White Bread Sliced 650g", "1.09"),
    ("Bread", "BREAD-RYE-500", "Rye Bread 500g", "1.59"),
    ("Pastry", "BANITSA-CHEESE", "Banitsa with Cheese 150g", "1.29"),
    ("Pastry", "CROISSANT-BUTTER", "Butter Croissant", "0.89"),
    ("Fruit", "APPLES-RED-1KG", "Red Apples 1kg", "1.99"),
    ("Fruit", "BANANAS-1KG", "Bananas 1kg", "1.69"),
    ("Vegetables", "TOMATOES-1KG", "Pink Tomatoes 1kg", "2.99"),
    ("Vegetables", "CUCUMBERS-1KG", "Cucumbers 1kg", "1.79"),
    ("Water", "WATER-MINERAL-15", "Mineral Water 1.5L", "0.55"),
    ("Water", "WATER-SPARKLING-05", "Sparkling Water 0.5L", "0.49"),
    ("Juice", "JUICE-ORANGE-1L", "Orange Juice 100% 1L", "2.29"),
    ("Juice", "JUICE-APPLE-1L", "Apple Juice 100% 1L", "1.99"),
    ("Coffee & Tea", "COFFEE-GROUND-250", "Ground Coffee 250g", "4.99"),
    ("Coffee & Tea", "TEA-HERBAL-20", "Herbal Tea x20", "1.89"),
    ("Cleaning", "DISH-SOAP-450", "Dishwashing Liquid 450ml", "1.75"),
]


class Command(BaseCommand):
    help = "Fill the catalog with a small grocery assortment for demos. Safe to run repeatedly."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        categories: dict[str, Category] = {}
        self._create_tree(TREE, parent=None, created=categories)

        for category_name, sku, title, price in PRODUCTS:
            Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "title": title,
                    "price": Decimal(price),
                    "category": categories[category_name],
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Catalog ready: {Category.objects.count()} categories, "
                f"{Product.objects.count()} products."
            )
        )

    def _create_tree(self, tree: dict, parent: Category | None, created: dict) -> None:
        for name, subtree in tree.items():
            category = Category.objects.filter(parent=parent, name=name).first()
            if category is None:
                # Through the service, like every other writer of the tree.
                category = create_category(name=name, parent=parent)
            created[name] = category
            self._create_tree(subtree, parent=category, created=created)
