from marshmallow import Schema, fields, validate


class ArticleSchema(Schema):
    id = fields.Int(dump_only=True)
    title = fields.Str(required=True, validate=validate.Length(min=1))
    slug = fields.Str(required=True, validate=validate.Length(min=1))
    published_label = fields.Str(required=True, validate=validate.Length(min=1))
    post_entry = fields.Str(required=True)
    tags = fields.List(fields.Str(), required=True)
    author_id = fields.Int(required=True)
    author = fields.Nested("AuthorSchema", only=("id", "name"), dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


class ArticleInputSchema(Schema):
    article = fields.Nested(ArticleSchema(exclude=("id", "created_at", "updated_at")))

