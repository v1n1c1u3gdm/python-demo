import json
from datetime import datetime
from pathlib import Path

from extensions import db
from models import Article, Author, SeedRun, Social
from seeds.bootstrap import bootstrap_seed_data
from seeds.data import AUTHOR_SEED, SEED_NAME, SOCIALS_SEED


def test_bootstrap_inserts_seed_records_and_uses_environment_public_key(app, monkeypatch):
    # Arrange
    monkeypatch.setenv("VINICIUS_PUBLIC_KEY", "ssh-ed25519 environment-key")
    article_seed = json.loads((Path(__file__).parents[2] / "seeds" / "article_seed_data.json").read_text())
    expected_article = article_seed["data"][0]
    expected_timestamp = datetime.fromisoformat(article_seed["metadata"]["generated_at"].replace("Z", "+00:00"))

    # Act
    with app.app_context():
        bootstrap_seed_data()

    # Assert
    with app.app_context():
        author = Author.query.one()
        articles = Article.query.order_by(Article.slug).all()
        socials = Social.query.order_by(Social.slug).all()
        seed_run = SeedRun.query.one()
        article = Article.query.filter_by(slug=expected_article["slug"]).one()
        assert author.name == AUTHOR_SEED["name"]
        assert author.public_key == "ssh-ed25519 environment-key"
        assert len(articles) == len(article_seed["data"])
        assert len(socials) == len(SOCIALS_SEED)
        assert seed_run.name == SEED_NAME
        assert seed_run.executed_at is not None
        assert author.created_at is not None
        assert author.updated_at is not None
        assert article.title == expected_article["title"]
        assert article.tags == expected_article["tags"]
        assert article.created_at == expected_timestamp.replace(tzinfo=None)
        assert article.updated_at == expected_timestamp.replace(tzinfo=None)


def test_bootstrap_skips_records_when_seed_run_already_exists(app):
    # Arrange
    with app.app_context():
        bootstrap_seed_data()
        author = Author.query.one()
        author.name = "Edited after seed"
        db.session.commit()

    # Act
    with app.app_context():
        bootstrap_seed_data()

    # Assert
    with app.app_context():
        assert Author.query.count() == 1
        assert Author.query.one().name == "Edited after seed"
        assert SeedRun.query.filter_by(name=SEED_NAME).count() == 1


def test_bootstrap_updates_existing_records_before_recording_seed(app, monkeypatch):
    # Arrange
    with app.app_context():
        bootstrap_seed_data()
        author = Author.query.one()
        author.public_key = "stale-key"
        social = Social.query.filter_by(slug="twitter").one()
        social.profile_link = "https://example.invalid/stale"
        article = Article.query.order_by(Article.slug).first()
        stale_article_slug = article.slug
        article.title = "Stale title"
        db.session.delete(SeedRun.query.one())
        db.session.commit()
    monkeypatch.setenv("VINICIUS_PUBLIC_KEY", "ssh-ed25519 refreshed-key")

    # Act
    with app.app_context():
        bootstrap_seed_data()

    # Assert
    with app.app_context():
        author = Author.query.one()
        social = Social.query.filter_by(slug="twitter").one()
        article = Article.query.filter_by(slug=stale_article_slug).one()
        assert author.name == AUTHOR_SEED["name"]
        assert author.public_key == "ssh-ed25519 refreshed-key"
        assert Author.query.count() == 1
        assert social.profile_link == next(item["profile_link"] for item in SOCIALS_SEED if item["slug"] == "twitter")
        assert article.title != "Stale title"
        assert SeedRun.query.filter_by(name=SEED_NAME).count() == 1
