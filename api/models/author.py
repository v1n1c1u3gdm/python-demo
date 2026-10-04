from extensions import db

from .base import SerializerMixin, TimestampMixin


class Author(SerializerMixin, TimestampMixin, db.Model):
    __tablename__ = "authors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    birthdate = db.Column(db.Date, nullable=False)
    photo_url = db.Column(db.String(512), nullable=False)
    public_key = db.Column(db.Text, nullable=False)
    bio = db.Column(db.Text, nullable=False)
    bypass_sanitization = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    identity = db.relationship(
        "AuthorIdentity",
        back_populates="author",
        cascade="all, delete-orphan",
        uselist=False,
    )
    socials = db.relationship(
        "Social",
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    articles = db.relationship(
        "Article",
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
