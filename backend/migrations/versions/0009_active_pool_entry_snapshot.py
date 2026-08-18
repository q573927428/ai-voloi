"""记录交易对进入活跃池的时间及 Signal 入池时间快照。"""

from alembic import op
import sqlalchemy as sa


revision = "0009_active_pool_entry_snapshot"
down_revision = "0008_signal_fund_flow_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加当前活跃周期起点和 Signal 不可变快照；旧数据保留为空。"""
    op.add_column("symbols", sa.Column("active_since", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_symbols_active_since", "symbols", ["active_since"], unique=False)
    op.add_column(
        "signals",
        sa.Column("active_pool_entered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """移除活跃池时间跟踪字段。"""
    op.drop_column("signals", "active_pool_entered_at")
    op.drop_index("ix_symbols_active_since", table_name="symbols")
    op.drop_column("symbols", "active_since")
