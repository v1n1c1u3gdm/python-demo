from flask import Blueprint, current_app, request
from marshmallow import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db
from models import Article, Author
from schemas import ArticleSchema
from services.authorization import (
    AuthorizationError,
    get_bearer_claims,
    has_role,
    require_author_identity,
)
from services.html_sanitizer import sanitize_html

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
    is_admin, author_id = _article_writer()
    payload = _load_article_payload(
        allow_missing_author=not is_admin,
        reject_author_id=not is_admin,
        reject_bypass=not is_admin,
    )
    if not is_admin:
        payload["author_id"] = author_id
        payload["bypass_sanitization"] = False
    if payload.get("bypass_sanitization") is not True:
        payload["bypass_sanitization"] = False
        payload["post_entry"] = sanitize_html(payload["post_entry"])
    _ensure_author_exists(payload["author_id"])

    article = Article(**payload)
    db.session.add(article)
    db.session.flush()
    return _commit_and_respond(article_schema.dump(article), status=201)


@bp.patch("/<int:article_id>")
def update_article(article_id: int):
    is_admin, author_id = _article_writer()
    article = Article.query.get(article_id)
    if not article:
        return error_response("Artigo não encontrado.", status=404)

    if not is_admin and article.author_id != author_id:
        raise AuthorizationError("Insufficient permissions.", 403)

    payload = _load_article_payload(
        partial=True,
        reject_author_id=not is_admin,
        reject_bypass=not is_admin,
    )

    if "author_id" in payload:
        _ensure_author_exists(payload["author_id"])

    if not is_admin and "post_entry" in payload:
        payload["bypass_sanitization"] = False
        payload["post_entry"] = sanitize_html(payload["post_entry"])
    elif is_admin:
        next_bypass = payload.get("bypass_sanitization", article.bypass_sanitization)
        if not next_bypass:
            post_entry = payload.get("post_entry", article.post_entry)
            payload["post_entry"] = sanitize_html(post_entry)

    for key, value in payload.items():
        setattr(article, key, value)

    return _commit_and_respond(article_schema.dump(article))


@bp.delete("/<int:article_id>")
def delete_article(article_id: int):
    is_admin, author_id = _article_writer()
    article = Article.query.get(article_id)
    if not article:
        return error_response("Artigo não encontrado.", status=404)

    if not is_admin and article.author_id != author_id:
        raise AuthorizationError("Insufficient permissions.", 403)

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


def _load_article_payload(
    partial: bool = False,
    *,
    allow_missing_author: bool = False,
    reject_author_id: bool = False,
    reject_bypass: bool = False,
):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError({"article": ["é obrigatório"]})
    article_data = body.get("article")
    if not isinstance(article_data, dict):
        raise ValidationError({"article": ["é obrigatório"]})
    if reject_author_id and "author_id" in article_data:
        raise ValidationError({"author_id": ["não pode ser informado pelo autor"]})
    if reject_bypass and "bypass_sanitization" in article_data:
        raise ValidationError({"bypass_sanitization": ["não pode ser informado pelo autor"]})

    schema_partial = partial or (("author_id",) if allow_missing_author else False)
    schema = ArticleSchema(partial=schema_partial)
    return schema.load(article_data)


def _article_writer() -> tuple[bool, int | None]:
    claims = get_bearer_claims()
    admin_role = current_app.config.get("KEYCLOAK_ADMIN_ROLE", "admin")
    if has_role(claims, admin_role):
        return True, None
    _, author_id = require_author_identity()
    return False, author_id


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
