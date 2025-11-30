from extensions import db
from .base import SerializerMixin


class SeedRun(SerializerMixin, db.Model):
    __tablename__ = "seed_runs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    executed_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), nullable=False)

