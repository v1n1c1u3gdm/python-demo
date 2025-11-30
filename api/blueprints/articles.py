from flask import Blueprint, request
from marshmallow import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db
from models import Article, Author
from schemas import ArticleSchema
from .utils import error_response, to_json


bp = Blueprint("articles", __name__, url_prefix="/articles")

article_schema = ArticleSchema()
article_list_schema = ArticleSchema(many=True)


@bp.get("")
def list_articles():
    articles = (
        Article.query.options(selectinload(Article.author))
        .order_by(Article.created_at.desc())
        .all()
    )
    return to_json(article_list_schema.dump(articles))


@bp.get("/<int:article_id>")
def get_article(article_id: int):
    article = (
        Article.query.options(selectinload(Article.author))
        .filter_by(id=article_id)
        .first()
    )
    if not article:
        return error_response("Artigo não encontrado.", status=404)
    return to_json(article_schema.dump(article))


@bp.post("")
def create_article():
    payload = _load_article_payload()
    _ensure_author_exists(payload["author_id"])

    article = Article(**payload)
    db.session.add(article)
    db.session.flush()
    return _commit_and_respond(article_schema.dump(article), status=201)


@bp.patch("/<int:article_id>")
def update_article(article_id: int):
    payload = _load_article_payload(partial=True)
    article = Article.query.get(article_id)
    if not article:
        return error_response("Artigo não encontrado.", status=404)

    if "author_id" in payload:
        _ensure_author_exists(payload["author_id"])

    for key, value in payload.items():
        setattr(article, key, value)

    return _commit_and_respond(article_schema.dump(article))


@bp.delete("/<int:article_id>")
def delete_article(article_id: int):
    article = Article.query.get(article_id)
    if not article:
        return error_response("Artigo não encontrado.", status=404)

    db.session.delete(article)
    return _commit_and_respond({}, status=204)


@bp.get("/count_by_author")
def count_by_author():
    results = (
        db.session.query(
            Author.id.label("author_id"),
            Author.name.label("author_name"),
            func.count(Article.id).label("articles_count"),
        )
        .outerjoin(Article)
        .group_by(Author.id)
        .order_by(Author.name.asc())
        .all()
    )

    payload = [
        {
            "author_id": row.author_id,
            "author_name": row.author_name,
            "articles_count": int(row.articles_count or 0),
        }
        for row in results
    ]
    return to_json(payload)


def _load_article_payload(partial: bool = False):
    body = request.get_json(silent=True) or {}
    if "article" not in body:
        raise ValidationError({"article": ["é obrigatório"]})

    schema = ArticleSchema(partial=partial)
    return schema.load(body["article"])


def _ensure_author_exists(author_id: int):
    exists = Author.query.filter_by(id=author_id).first()
    if not exists:
        raise ValidationError({"author_id": ["Author must exist"]})


def _commit_and_respond(payload, status=200):
    try:
        db.session.commit()
        if status == 204:
            return ("", status)
        return to_json(payload, status=status)
    except IntegrityError as exc:
        db.session.rollback()
        detail = str(exc.orig).lower()
        if "slug" in detail:
            return error_response("Slug has already been taken")
        return error_response("Não foi possível salvar o artigo.")

