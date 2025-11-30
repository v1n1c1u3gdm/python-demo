from flask import Blueprint, request
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Author, Social
from schemas import SocialSchema
from .utils import error_response, to_json


bp = Blueprint("socials", __name__, url_prefix="/socials")

social_schema = SocialSchema()
social_list_schema = SocialSchema(many=True)


@bp.get("")
def list_socials():
    socials = Social.query.order_by(Social.slug.asc()).all()
    return to_json(social_list_schema.dump(socials))


@bp.get("/<int:social_id>")
def get_social(social_id: int):
    social = Social.query.get(social_id)
    if not social:
        return error_response("Social não encontrado.", status=404)
    return to_json(social_schema.dump(social))


@bp.post("")
def create_social():
    payload = _load_social_payload()
    _ensure_author_exists(payload["author_id"])

    social = Social(**payload)
    db.session.add(social)
    db.session.flush()
    return _commit_and_respond(social_schema.dump(social), status=201)


@bp.patch("/<int:social_id>")
def update_social(social_id: int):
    payload = _load_social_payload(partial=True)
    social = Social.query.get(social_id)
    if not social:
        return error_response("Social não encontrado.", status=404)

    if "author_id" in payload:
        _ensure_author_exists(payload["author_id"])

    for key, value in payload.items():
        setattr(social, key, value)

    return _commit_and_respond(social_schema.dump(social))


@bp.delete("/<int:social_id>")
def delete_social(social_id: int):
    social = Social.query.get(social_id)
    if not social:
        return error_response("Social não encontrado.", status=404)

    db.session.delete(social)
    return _commit_and_respond({}, status=204)


def _load_social_payload(partial: bool = False):
    body = request.get_json(silent=True) or {}
    if "social" not in body:
        raise ValidationError({"social": ["é obrigatório"]})
    schema = SocialSchema(partial=partial)
    return schema.load(body["social"])


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
        return error_response("Não foi possível salvar o social.")

