"""为交易对市场快照增加资金费率。"""

import sqlalchemy as sa
from alembic import op

revision = "0005_symbol_funding_rate"
down_revision = "0004_future_performance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保存 Binance premium index 返回的最近一次资金费率。"""
    op.add_column(
        "symbols",
        sa.Column("funding_rate", sa.Numeric(36, 12), nullable=True),
    )


def downgrade() -> None:
    """移除交易对资金费率快照。"""
    op.drop_column("symbols", "funding_rate")
