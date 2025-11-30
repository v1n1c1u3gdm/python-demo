from datetime import datetime

from extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SerializerMixin:
    def to_dict(self):
        data = {c.key: getattr(self, c.key) for c in self.__table__.columns}
        return data

