"""Add HTML sanitization bypass flags without changing stored content."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20261004_0003"
down_revision = "20261004_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "articles",
        sa.Column("bypass_sanitization", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "authors",
        sa.Column("bypass_sanitization", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade():
    op.drop_column("authors", "bypass_sanitization")
    op.drop_column("articles", "bypass_sanitization")
