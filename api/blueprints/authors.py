from flask import Blueprint, request
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db
from models import Author
from schemas import AuthorSchema
from .utils import error_response, not_found, to_json


bp = Blueprint("authors", __name__, url_prefix="/authors")

author_schema = AuthorSchema()
author_list_schema = AuthorSchema(exclude=("articles",), many=True)


@bp.get("")
def list_authors():
    authors = (
        Author.query.options(
            selectinload(Author.socials),
            selectinload(Author.articles),
        )
        .order_by(Author.name.asc())
        .all()
    )
    return to_json(author_list_schema.dump(authors))


@bp.get("/<int:author_id>")
def get_author(author_id: int):
    author = (
        Author.query.options(
            selectinload(Author.socials),
            selectinload(Author.articles),
        )
        .filter_by(id=author_id)
        .first()
    )
    if not author:
        not_found("Autor")
    return to_json(author_schema.dump(author))


@bp.post("")
def create_author():
    payload = _load_author_payload()
    author = Author(**payload)
    db.session.add(author)
    db.session.flush()
    return _commit_and_respond(author_schema.dump(author), status=201)


@bp.patch("/<int:author_id>")
def update_author(author_id: int):
    payload = _load_author_payload(partial=True)
    author = Author.query.get(author_id)
    if not author:
        not_found("Autor")

    for key, value in payload.items():
        setattr(author, key, value)

    return _commit_and_respond(author_schema.dump(author))


@bp.delete("/<int:author_id>")
def delete_author(author_id: int):
    author = Author.query.get(author_id)
    if not author:
        not_found("Autor")

    db.session.delete(author)
    return _commit_and_respond({}, status=204)


def _load_author_payload(partial: bool = False):
    body = request.get_json(silent=True) or {}
    if "author" not in body:
        raise ValidationError({"author": ["é obrigatório"]})

    schema = AuthorSchema(partial=partial)
    return schema.load(body["author"])


def _commit_and_respond(payload, status=200):
    try:
        db.session.commit()
        if status == 204:
            return ("", status)
        return to_json(payload, status=status)
    except IntegrityError as exc:
        db.session.rollback()
        detail = str(exc.orig).lower()
        if "authors.name" in detail or "unique" in detail:
            return error_response("Name has already been taken")
        return error_response("Não foi possível salvar o autor.")

