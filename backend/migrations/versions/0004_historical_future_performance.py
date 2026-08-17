"""增加未来表现周期，并切换为目标时刻历史价格口径。"""

import sqlalchemy as sa
from alembic import op

revision = "0004_historical_future_performance"
down_revision = "0003_signal_oi_lookback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加 8h、12h、16h、2d，并清空旧口径派生值以触发准确回填。"""
    for column_name in ("return_8h", "return_12h", "return_16h", "return_2d"):
        op.add_column(
            "signal_future_performance",
            sa.Column(column_name, sa.Numeric(36, 12), nullable=True),
        )

    # 旧值可能由同一次实时价格批量写入，无法判断哪些可靠，因此统一按历史 K 线重新计算。
    op.execute(
        sa.text(
            """
            UPDATE signal_future_performance
            SET return_5m = NULL,
                return_15m = NULL,
                return_30m = NULL,
                return_1h = NULL,
                return_4h = NULL,
                return_1d = NULL,
                max_profit_percent = NULL,
                max_loss_percent = NULL
            """
        )
    )


def downgrade() -> None:
    """移除新增观察周期；旧口径派生值无法在降级时恢复。"""
    for column_name in reversed(("return_8h", "return_12h", "return_16h", "return_2d")):
        op.drop_column("signal_future_performance", column_name)
