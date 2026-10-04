"""Store private Keycloak identity links for authors."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import VARBINARY

# revision identifiers, used by Alembic.
revision = "20261004_0002"
down_revision = "20251130_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "author_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column(
            "issuer",
            VARBINARY(512).with_variant(sa.LargeBinary(512), "sqlite"),
            nullable=False,
        ),
        sa.Column(
            "subject",
            VARBINARY(255).with_variant(sa.LargeBinary(255), "sqlite"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", name="uq_author_identities_author_id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_author_identities_issuer_subject"),
    )


def downgrade():
    op.drop_table("author_identities")
