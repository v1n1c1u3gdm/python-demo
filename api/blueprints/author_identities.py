from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Author, AuthorIdentity
from services.authorization import require_admin

bp = Blueprint("author_identities", __name__, url_prefix="/authors")


@bp.put("/<int:author_id>/identity")
def link_author_identity(author_id: int):
    require_admin()
    payload = request.get_json(silent=True)
    identity = payload.get("identity") if isinstance(payload, dict) else None
    if not isinstance(identity, dict) or set(identity) != {"issuer", "sub"}:
        return jsonify({"errors": ["A valid identity is required."]}), 400

    issuer = identity["issuer"]
    subject = identity["sub"]
    if not isinstance(issuer, str) or not isinstance(subject, str):
        return jsonify({"errors": ["A valid identity is required."]}), 400
    if issuer != current_app.config["KEYCLOAK_ISSUER"]:
        return jsonify({"errors": ["Identity issuer is not configured."]}), 400

    try:
        issuer_bytes = issuer.encode("utf-8")
        subject_bytes = subject.encode("utf-8")
    except UnicodeEncodeError:
        return jsonify({"errors": ["A valid identity is required."]}), 400
    if not issuer_bytes or len(issuer_bytes) > 512 or not subject_bytes or len(subject_bytes) > 255:
        return jsonify({"errors": ["A valid identity is required."]}), 400

    author = db.session.get(Author, author_id)
    if author is None:
        return jsonify({"errors": ["Autor não encontrado."]}), 404

    linked_identity = db.session.query(AuthorIdentity).filter_by(author_id=author_id).one_or_none()
    if linked_identity is None:
        linked_identity = AuthorIdentity(author_id=author_id)
        db.session.add(linked_identity)
    linked_identity.issuer = issuer_bytes
    linked_identity.subject = subject_bytes

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"errors": ["Identity is already linked to another author."]}), 409

    return jsonify({"author_id": author_id, "linked": True})


@bp.delete("/<int:author_id>/identity")
def revoke_author_identity(author_id: int):
    require_admin()
    author = db.session.get(Author, author_id)
    if author is None:
        return jsonify({"errors": ["Autor não encontrado."]}), 404

    identity = db.session.query(AuthorIdentity).filter_by(author_id=author_id).one_or_none()
    if identity is not None:
        db.session.delete(identity)
        db.session.commit()
    return "", 204
