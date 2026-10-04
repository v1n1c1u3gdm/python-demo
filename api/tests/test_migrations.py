from pathlib import Path

import sqlalchemy as sa
from flask import Flask
from flask_migrate import Migrate, downgrade, upgrade

import models  # noqa: F401
from extensions import db


def schema_tables(database_path):
    engine = sa.create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            return set(sa.inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_initial_migration_creates_expected_schema_and_can_be_downgraded(tmp_path):
    # Arrange
    database_path = tmp_path / "migration-test.sqlite"
    app = Flask("migration-test")
    app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}")
    db.init_app(app)
    migration_directory = Path(__file__).parents[1] / "migrations"
    Migrate(app, db, directory=str(migration_directory))

    # Act
    with app.app_context():
        upgrade(revision="20251130_0001")

    # Assert
    engine = sa.create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names()) == {
                "alembic_version", "authors", "articles", "socials", "seed_runs"
            }
            assert {column["name"] for column in inspector.get_columns("authors")} == {
                "id", "name", "birthdate", "photo_url", "public_key", "bio", "created_at", "updated_at"
            }
            assert {column["name"] for column in inspector.get_columns("articles")} >= {
                "id", "slug", "tags", "author_id", "created_at", "updated_at"
            }
    finally:
        engine.dispose()

    # Act
    with app.app_context():
        downgrade(revision="base")

    # Assert
    assert schema_tables(database_path) == {"alembic_version"}
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_author_identity_migration_preserves_existing_authors(tmp_path):
    # Arrange
    database_path = tmp_path / "author-identities.sqlite"
    app = Flask("migration-test")
    app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}")
    db.init_app(app)
    migration_directory = Path(__file__).parents[1] / "migrations"
    Migrate(app, db, directory=str(migration_directory))

    # Act
    with app.app_context():
        upgrade(revision="20251130_0001")
        with db.engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO authors "
                    "(id, name, birthdate, photo_url, public_key, bio, created_at, updated_at) "
                    "VALUES (17, 'Legacy author', '2000-01-01', 'https://example.test/p.png', "
                    "'public-key', 'legacy bio', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        upgrade()

    # Assert
    engine = sa.create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT name FROM authors WHERE id = 17")
            ).scalar_one() == "Legacy author"
            assert "author_identities" in sa.inspect(connection).get_table_names()
            identity_columns = {
                column["name"] for column in sa.inspect(connection).get_columns("author_identities")
            }
            assert identity_columns == {"id", "author_id", "issuer", "subject"}
    finally:
        engine.dispose()

    # Act
    with app.app_context():
        downgrade(revision="20251130_0001")

    # Assert
    engine = sa.create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT name FROM authors WHERE id = 17")
            ).scalar_one() == "Legacy author"
            assert "author_identities" not in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
