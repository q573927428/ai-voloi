"""将 Signal 未来表现统一为无持仓方向的价格变化语义。"""

from alembic import op

revision = "0007_price_change_semantics"
down_revision = "0006_signal_indicator_snapshot"
branch_labels = None
depends_on = None


HORIZON_RENAMES = {
    "return_5m": "price_change_5m_percent",
    "return_15m": "price_change_15m_percent",
    "return_30m": "price_change_30m_percent",
    "return_1h": "price_change_1h_percent",
    "return_4h": "price_change_4h_percent",
    "return_8h": "price_change_8h_percent",
    "return_12h": "price_change_12h_percent",
    "return_16h": "price_change_16h_percent",
    "return_1d": "price_change_1d_percent",
    "return_2d": "price_change_2d_percent",
}


def upgrade() -> None:
    """仅修正字段语义，完整保留已有时点变化及最大值和最小值。"""
    for old_name, new_name in HORIZON_RENAMES.items():
        op.alter_column(
            "signal_future_performance",
            old_name,
            new_column_name=new_name,
        )
    op.alter_column(
        "signal_future_performance",
        "max_profit_percent",
        new_column_name="max_rise_percent",
    )
    op.alter_column(
        "signal_future_performance",
        "max_loss_percent",
        new_column_name="max_drop_percent",
    )


def downgrade() -> None:
    """恢复旧字段名，数值不发生变化。"""
    op.alter_column(
        "signal_future_performance",
        "max_rise_percent",
        new_column_name="max_profit_percent",
    )
    op.alter_column(
        "signal_future_performance",
        "max_drop_percent",
        new_column_name="max_loss_percent",
    )
    for old_name, new_name in reversed(HORIZON_RENAMES.items()):
        op.alter_column(
            "signal_future_performance",
            new_name,
            new_column_name=old_name,
        )
