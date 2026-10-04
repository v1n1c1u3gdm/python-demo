import logging

import click
from flask import Flask
from flask_migrate import upgrade

from seeds import bootstrap_seed_data


def register_bootstrap_command(app: Flask) -> None:
    @app.cli.command("bootstrap-db")
    def bootstrap_database() -> None:
        try:
            _upgrade_database()
        except (Exception, SystemExit):
            raise click.ClickException("Database migration failed.") from None

        try:
            bootstrap_seed_data()
        except Exception:
            raise click.ClickException("Database seed failed.") from None


def _upgrade_database() -> None:
    migration_logger = logging.getLogger("flask_migrate")
    was_disabled = migration_logger.disabled
    migration_logger.disabled = True
    try:
        upgrade()
    finally:
        migration_logger.disabled = was_disabled
