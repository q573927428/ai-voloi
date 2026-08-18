"""为 K 线补充主动买入成交额，并固化 Signal 资金流与资金费率快照。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_signal_fund_flow_snapshot"
down_revision = "0007_price_change_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增字段保持可空，旧历史数据不会被伪造成零资金流。"""
    op.add_column("klines", sa.Column("taker_buy_quote_volume", sa.Numeric(36, 12), nullable=True))
    op.add_column("signals", sa.Column("fund_flow_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("signals", sa.Column("funding_rate", sa.Numeric(36, 12), nullable=True))


def downgrade() -> None:
    """移除本版本新增的资金流和资金费率快照字段。"""
    op.drop_column("signals", "funding_rate")
    op.drop_column("signals", "fund_flow_snapshot")
    op.drop_column("klines", "taker_buy_quote_volume")
