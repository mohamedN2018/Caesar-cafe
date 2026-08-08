"""
    python manage.py restore_database <filename> --i-understand-this-destroys-data

A management command and never an endpoint. An HTTP route that replaces the
database is a route somebody eventually calls by mistake, and the mistake is
unrecoverable.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ops import backups


class Command(BaseCommand):
    help = "Restore the database from a backup file. DESTROYS the current contents."

    def add_arguments(self, parser) -> None:
        parser.add_argument("filename", help="A file in the backup directory.")
        parser.add_argument(
            "--i-understand-this-destroys-data",
            action="store_true",
            dest="confirmed",
            help="Required. Without it the command explains and exits.",
        )

    def handle(self, *args, **options) -> None:
        filename = options["filename"]

        if not options["confirmed"]:
            path = backups.backup_dir() / filename
            raise CommandError(
                f"This would replace every row in the database with the contents of\n"
                f"  {path}\n"
                "and cannot be undone. Re-run with "
                "--i-understand-this-destroys-data if that is what you intend.\n"
                "Take a fresh backup first: python manage.py backup_database --label pre-restore"
            )

        self.stdout.write(f"restoring from {filename} …")
        backups.restore(filename, confirmed=True)
        self.stdout.write(self.style.SUCCESS("restore complete"))
        self.stdout.write(
            "Now verify, in this order (docs/13): health endpoint, admin login, a known "
            "past week's sales summary, the audit log, and a rollup rebuild."
        )
