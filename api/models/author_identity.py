from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.mysql import VARBINARY

from extensions import db


class AuthorIdentity(db.Model):
    __tablename__ = "author_identities"
    __table_args__ = (
        UniqueConstraint("author_id", name="uq_author_identities_author_id"),
        UniqueConstraint("issuer", "subject", name="uq_author_identities_issuer_subject"),
    )

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(
        db.Integer,
        db.ForeignKey("authors.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer = db.Column(
        VARBINARY(512).with_variant(db.LargeBinary(512), "sqlite"),
        nullable=False,
    )
    subject = db.Column(
        VARBINARY(255).with_variant(db.LargeBinary(255), "sqlite"),
        nullable=False,
    )

    author = db.relationship("Author", back_populates="identity")


def find_author_id(issuer: str, subject: str) -> int | None:
    try:
        issuer_bytes = issuer.encode("utf-8")
        subject_bytes = subject.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        return None

    if not issuer_bytes or len(issuer_bytes) > 512 or not subject_bytes or len(subject_bytes) > 255:
        return None

    identity = AuthorIdentity.query.filter_by(issuer=issuer_bytes, subject=subject_bytes).first()
    return identity.author_id if identity else None
