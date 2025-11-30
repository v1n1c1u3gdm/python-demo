from marshmallow import Schema, fields, validate

from .article import ArticleSchema
from .social import SocialSchema


class AuthorSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1))
    birthdate = fields.Date(required=True)
    photo_url = fields.Url(required=True)
    public_key = fields.Str(required=True)
    bio = fields.Str(required=True)
    socials = fields.List(fields.Nested(lambda: SocialSchema(exclude=("author_id",))))
    articles = fields.List(fields.Nested(lambda: ArticleSchema(exclude=("author",))))
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


class AuthorInputSchema(Schema):
    author = fields.Nested(
        AuthorSchema(
            exclude=("id", "socials", "articles", "created_at", "updated_at"),
        )
    )

