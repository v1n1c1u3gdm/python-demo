from marshmallow import Schema, fields, validate


class SocialSchema(Schema):
    id = fields.Int(dump_only=True)
    profile_link = fields.Url(required=True)
    slug = fields.Str(required=True, validate=validate.Length(min=1))
    description = fields.Str(required=True)
    author_id = fields.Int(required=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


class SocialInputSchema(Schema):
    social = fields.Nested(
        SocialSchema(
            exclude=("id", "created_at", "updated_at"),
        )
    )

