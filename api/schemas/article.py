from marshmallow import Schema, ValidationError, fields, post_dump, validate

from services.html_sanitizer import sanitize_html


class StrictBoolean(fields.Field):
    """Accept JSON booleans without Marshmallow's string/number coercion."""

    def _serialize(self, value, attr, obj, **kwargs):
        return bool(value)

    def _deserialize(self, value, attr, data, **kwargs):
        if type(value) is not bool:
            raise ValidationError("Must be a JSON boolean.")
        return value


class ArticleSchema(Schema):
    id = fields.Int(dump_only=True)
    title = fields.Str(required=True, validate=validate.Length(min=1))
    slug = fields.Str(required=True, validate=validate.Length(min=1))
    published_label = fields.Str(required=True, validate=validate.Length(min=1))
    post_entry = fields.Str(required=True)
    bypass_sanitization = StrictBoolean()
    tags = fields.List(fields.Str(), required=True)
    author_id = fields.Int(required=True)
    author = fields.Nested("AuthorSchema", only=("id", "name"), dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @post_dump
    def sanitize_public_content(self, data, **kwargs):
        if not data.get("bypass_sanitization", False) and "post_entry" in data:
            data["post_entry"] = sanitize_html(data["post_entry"])
        return data


class ArticleInputSchema(Schema):
    article = fields.Nested(ArticleSchema(exclude=("id", "created_at", "updated_at")))
