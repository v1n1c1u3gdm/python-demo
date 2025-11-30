import json
from datetime import datetime
from pathlib import Path

from flask import current_app

from extensions import db
from models import Article, Author, SeedRun, Social
from .data import AUTHOR_SEED, SEED_NAME, SOCIALS_SEED


def bootstrap_seed_data():
    """Load deterministic data into the database if the seed has not been executed yet."""

    seed_exists = SeedRun.query.filter_by(name=SEED_NAME).first()
    if seed_exists:
        current_app.logger.info("Seed '%s' already applied, skipping.", SEED_NAME)
        return

    current_app.logger.info("Running database seed '%s'...", SEED_NAME)
    author = _upsert_author()
    _upsert_socials(author)
    _upsert_articles(author)

    db.session.add(SeedRun(name=SEED_NAME))
    db.session.commit()
    current_app.logger.info("Seed '%s' completed.", SEED_NAME)


def _upsert_author():
    from os import getenv

    payload = AUTHOR_SEED.copy()
    payload["public_key"] = getenv("VINICIUS_PUBLIC_KEY", payload["public_key"])
    payload["birthdate"] = datetime.fromisoformat(payload["birthdate"]).date()

    author = Author.query.filter_by(name=payload["name"]).first()
    if author:
        for key, value in payload.items():
            setattr(author, key, value)
    else:
        author = Author(**payload)
        db.session.add(author)
    db.session.flush()
    return author


def _upsert_socials(author: Author):
    for social_data in SOCIALS_SEED:
        social = Social.query.filter_by(slug=social_data["slug"]).first()
        if social is None:
            social = Social(author_id=author.id, **social_data)
            db.session.add(social)
        else:
            social.profile_link = social_data["profile_link"]
            social.description = social_data["description"]
            social.author_id = author.id
    db.session.flush()


def _upsert_articles(author: Author):
    payload = _load_article_seed_file()
    metadata = payload.get("metadata", {})
    generated_at = metadata.get("generated_at")
    generated_ts = (
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if generated_at
        else datetime.utcnow()
    )

    for article_attrs in payload.get("data", []):
        slug = article_attrs.get("slug")
        article = Article.query.filter_by(slug=slug).first()
        attrs = {
            "title": article_attrs.get("title"),
            "published_label": article_attrs.get("published_label"),
            "post_entry": article_attrs.get("post_entry"),
            "tags": article_attrs.get("tags") or [],
            "author_id": author.id,
        }
        if article:
            for key, value in attrs.items():
                setattr(article, key, value)
        else:
            article = Article(slug=slug, **attrs)
            db.session.add(article)

        article.created_at = generated_ts
        article.updated_at = generated_ts


def _load_article_seed_file():
    seed_path = Path(__file__).parent / "article_seed_data.json"
    with open(seed_path, "r", encoding="utf-8") as handler:
        return json.load(handler)

