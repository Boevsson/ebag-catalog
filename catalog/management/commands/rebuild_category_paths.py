from django.core.management.base import BaseCommand

from catalog.services.categories import rebuild_category_paths


class Command(BaseCommand):
    help = (
        "Recompute the materialized path of every category from its parent link. "
        "Needed only after something changed `parent` without going through the "
        "category service (bulk update, data migration, manual SQL)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the categories whose path is wrong without changing them.",
        )

    def handle(self, *args, dry_run: bool, **options) -> None:
        repaired = rebuild_category_paths(dry_run=dry_run)

        for category in repaired:
            self.stdout.write(f"  #{category.pk} {category.name}: {category.ancestor_path}")
        outcome = "would be repaired" if dry_run else "repaired"
        self.stdout.write(self.style.SUCCESS(f"{len(repaired)} path(s) {outcome}."))
