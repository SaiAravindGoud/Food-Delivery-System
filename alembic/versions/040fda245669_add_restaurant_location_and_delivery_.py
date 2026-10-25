"""Add restaurant location and delivery settings."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "040fda245669"
down_revision: Union[str, Sequence[str], None] = "56c2d6eeee51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "restaurants",
        sa.Column("cuisine_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "restaurants",
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
    )
    op.add_column(
        "restaurants",
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
    )
    op.add_column(
        "restaurants",
        sa.Column("opening_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "restaurants",
        sa.Column("closing_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "restaurants",
        sa.Column(
            "delivery_radius_km",
            sa.Numeric(precision=6, scale=2),
            nullable=False,
            server_default=sa.text("5.00"),
        ),
    )
    op.add_column(
        "restaurants",
        sa.Column(
            "min_order_value",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column(
        "restaurants",
        sa.Column(
            "avg_preparation_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )
    op.add_column(
        "restaurants",
        sa.Column(
            "is_temporarily_closed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("restaurants", "is_temporarily_closed")
    op.drop_column("restaurants", "avg_preparation_minutes")
    op.drop_column("restaurants", "min_order_value")
    op.drop_column("restaurants", "delivery_radius_km")
    op.drop_column("restaurants", "closing_time")
    op.drop_column("restaurants", "opening_time")
    op.drop_column("restaurants", "longitude")
    op.drop_column("restaurants", "latitude")
    op.drop_column("restaurants", "cuisine_type")
