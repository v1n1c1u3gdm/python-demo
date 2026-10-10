import logging
import runpy
import sqlite3

import pytest
from flask import Flask

import app as app_module
from config import BaseConfig
from extensions import db
from models import Author, SeedRun
from seeds.data import SEED_NAME


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    database_path = tmp_path / "application.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    class IsolatedConfig(BaseConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite+pysqlite:///{database_path}"
        TESTING = False

    monkeypatch.setattr(app_module, "get_config", lambda: IsolatedConfig)
    application = app_module.create_app()
    yield application
    with application.app_context():
        try:
            db.session.remove()
            db.drop_all()
        finally:
            db.engine.dispose()


def test_creating_non_testing_app_does_not_bootstrap_database(tmp_path, monkeypatch):
    # Arrange
    database_path = tmp_path / "application.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")

    class IsolatedConfig(BaseConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite+pysqlite:///{database_path}"
        TESTING = False

    calls = []
    monkeypatch.setattr(app_module, "get_config", lambda: IsolatedConfig)
    monkeypatch.setattr("bootstrap.upgrade", lambda: calls.append("migration"))
    monkeypatch.setattr("bootstrap.bootstrap_seed_data", lambda: calls.append("seed"))

    # Act
    application = app_module.create_app()

    # Assert
    assert calls == []
    assert not database_path.exists()
    assert application.config["SQLALCHEMY_DATABASE_URI"] == f"sqlite+pysqlite:///{database_path}"
    with application.app_context():
        db.engine.dispose()


def test_bootstrap_command_applies_migrations_before_seeds(isolated_app, monkeypatch):
    # Arrange
    app = isolated_app
    calls = []
    monkeypatch.setattr("bootstrap.upgrade", lambda: calls.append("migration"))
    monkeypatch.setattr("bootstrap.bootstrap_seed_data", lambda: calls.append("seed"))

    # Act
    with app.app_context():
        result = app.test_cli_runner().invoke(args=["bootstrap-db"])

    # Assert
    assert result.exit_code == 0, result.output
    assert calls == ["migration", "seed"]


def test_migration_failure_blocks_seed_and_hides_secrets(isolated_app, monkeypatch):
    # Arrange
    app = isolated_app
    calls = []

    def fail_migration():
        calls.append("migration")
        raise RuntimeError("mysql://user:super-secret@host/db")

    monkeypatch.setattr("bootstrap.upgrade", fail_migration)
    monkeypatch.setattr("bootstrap.bootstrap_seed_data", lambda: calls.append("seed"))

    # Act
    with app.app_context():
        result = app.test_cli_runner().invoke(args=["bootstrap-db"])

    # Assert
    assert result.exit_code != 0
    assert calls == ["migration"]
    assert "Database migration failed." in result.output
    assert "super-secret" not in result.output


def test_flask_migrate_wrapper_failure_hides_secrets_and_restores_logger(
    isolated_app, monkeypatch, caplog
):
    # Arrange
    import flask_migrate

    app = isolated_app
    migration_logger = logging.getLogger("flask_migrate")
    original_disabled = migration_logger.disabled

    def fail_alembic_upgrade(*args, **kwargs):
        raise RuntimeError("mysql://user:wrapper-secret@host/db")

    monkeypatch.setattr(flask_migrate.command, "upgrade", fail_alembic_upgrade)
    monkeypatch.setattr("bootstrap.bootstrap_seed_data", lambda: (_ for _ in ()).throw(AssertionError()))

    # Act
    with caplog.at_level(logging.ERROR, logger="flask_migrate"):
        result = app.test_cli_runner().invoke(args=["bootstrap-db"])

    # Assert
    assert result.exit_code != 0
    assert "Database migration failed." in result.output
    assert "wrapper-secret" not in result.output
    assert "wrapper-secret" not in caplog.text
    assert migration_logger.disabled is original_disabled



def test_flask_migrate_wrapper_success_restores_logger(isolated_app, monkeypatch):
    # Arrange
    import flask_migrate

    app = isolated_app
    migration_logger = logging.getLogger("flask_migrate")
    original_disabled = migration_logger.disabled
    seed_calls = []
    monkeypatch.setattr(flask_migrate.command, "upgrade", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "bootstrap.bootstrap_seed_data", lambda: seed_calls.append("seed")
    )

    # Act
    with app.app_context():
        result = app.test_cli_runner().invoke(args=["bootstrap-db"])

    # Assert
    assert result.exit_code == 0, result.output
    assert seed_calls == ["seed"]
    assert migration_logger.disabled is original_disabled

def test_seed_failure_is_reported_without_secrets(isolated_app, monkeypatch, caplog):
    # Arrange
    app = isolated_app
    calls = []

    def record_migration():
        calls.append("migration")

    def fail_seed():
        calls.append("seed")
        raise RuntimeError("token=seed-secret")

    monkeypatch.setattr("bootstrap.upgrade", record_migration)
    monkeypatch.setattr("bootstrap.bootstrap_seed_data", fail_seed)

    # Act
    with app.app_context(), caplog.at_level(logging.ERROR):
        result = app.test_cli_runner().invoke(args=["bootstrap-db"])

    # Assert
    assert result.exit_code != 0
    assert calls == ["migration", "seed"]
    assert "Database seed failed." in result.output
    assert "seed-secret" not in result.output
    assert "seed-secret" not in caplog.text


def test_bootstrap_command_preserves_edited_records_when_repeated(isolated_app, tmp_path):
    # Arrange
    app = isolated_app
    runner = app.test_cli_runner()
    database_path = tmp_path / "application.sqlite"

    # Act
    with app.app_context():
        first_result = runner.invoke(args=["bootstrap-db"])
    with app.app_context():
        author = Author.query.one()
        author.name = "Edited after bootstrap"
        db.session.commit()
    with app.app_context():
        second_result = runner.invoke(args=["bootstrap-db"])

    # Assert
    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    assert app.config["SQLALCHEMY_DATABASE_URI"] == f"sqlite+pysqlite:///{database_path}"
    assert database_path.exists()
    with app.app_context():
        assert Author.query.one().name == "Edited after bootstrap"
        assert SeedRun.query.filter_by(name=SEED_NAME).count() == 1
    with sqlite3.connect(database_path) as connection:
        persisted_authors = connection.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        persisted_seed_runs = connection.execute(
            "SELECT COUNT(*) FROM seed_runs WHERE name = ?", (SEED_NAME,)
        ).fetchone()[0]
    assert persisted_authors > 0
    assert persisted_seed_runs == 1


def test_failed_seed_can_be_retried_without_persisting_seed_run(isolated_app, monkeypatch):
    # Arrange
    app = isolated_app
    runner = app.test_cli_runner()
    import seeds.bootstrap as seed_module

    real_upsert_articles = seed_module._upsert_articles

    def fail_before_commit(author):
        raise RuntimeError("controlled seed failure")

    # Act
    monkeypatch.setattr(seed_module, "_upsert_articles", fail_before_commit)
    with app.app_context():
        failed_result = runner.invoke(args=["bootstrap-db"])
    with app.app_context():
        failed_seed_runs = SeedRun.query.filter_by(name=SEED_NAME).count()
        failed_authors = Author.query.count()
    monkeypatch.setattr(seed_module, "_upsert_articles", real_upsert_articles)
    with app.app_context():
        retry_result = runner.invoke(args=["bootstrap-db"])

    # Assert
    assert failed_result.exit_code != 0
    assert failed_seed_runs == 0
    assert failed_authors == 0
    assert retry_result.exit_code == 0
    with app.app_context():
        assert SeedRun.query.filter_by(name=SEED_NAME).count() == 1


def test_development_entrypoint_binds_only_to_loopback(monkeypatch):
    """The direct Flask development server must not expose a public bind."""
    calls = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: calls.append(kwargs))

    runpy.run_module("app", run_name="__main__")

    assert calls == [{"host": "127.0.0.1", "port": 5000}]
