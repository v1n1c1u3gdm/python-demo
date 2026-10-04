from marshmallow import Schema, fields, post_dump, validate

from services.html_sanitizer import sanitize_html

from .article import ArticleSchema, StrictBoolean
from .social import SocialSchema


class AuthorSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1))
    birthdate = fields.Date(required=True)
    photo_url = fields.Url(required=True)
    public_key = fields.Str(required=True)
    bio = fields.Str(required=True)
    bypass_sanitization = StrictBoolean()
    socials = fields.List(fields.Nested(lambda: SocialSchema(exclude=("author_id",))))
    articles = fields.List(fields.Nested(lambda: ArticleSchema(exclude=("author",))))
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @post_dump
    def sanitize_public_content(self, data, **kwargs):
        if not data.get("bypass_sanitization", False) and "bio" in data:
            data["bio"] = sanitize_html(data["bio"])
        return data


class AuthorInputSchema(Schema):
    author = fields.Nested(
        AuthorSchema(
            exclude=("id", "socials", "articles", "created_at", "updated_at"),
        )
    )
