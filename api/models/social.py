from extensions import db
from .base import SerializerMixin, TimestampMixin


class Social(SerializerMixin, TimestampMixin, db.Model):
    __tablename__ = "socials"

    id = db.Column(db.Integer, primary_key=True)
    profile_link = db.Column(db.String(512), nullable=False)
    slug = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id", ondelete="CASCADE"), nullable=False)

    author = db.relationship("Author", back_populates="socials")

