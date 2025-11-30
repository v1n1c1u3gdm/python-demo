from sqlalchemy import event
from sqlalchemy.orm import validates

from extensions import db
from .base import SerializerMixin, TimestampMixin


class Article(SerializerMixin, TimestampMixin, db.Model):
    __tablename__ = "articles"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False, unique=True)
    published_label = db.Column(db.String(255), nullable=False)
    post_entry = db.Column(db.Text, nullable=False)
    tags = db.Column(db.JSON, nullable=False, default=list)
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id", ondelete="CASCADE"), nullable=False)

    author = db.relationship("Author", back_populates="articles")

    @validates("tags")
    def validate_tags(self, key, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("Tags must be an array")
        return value


@event.listens_for(Article, "load")
def ensure_tags_list(article, _):
    if article.tags is None:
        article.tags = []

