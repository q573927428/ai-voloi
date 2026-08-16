"""为 Signal 快照增加价格技术指标字段。"""

import sqlalchemy as sa
from alembic import op

revision = "0002_signal_technical_indicators"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加 EMA、RSI、ADX、ATR 及斜率快照字段。"""
    for column_name in (
        "ema14",
        "ema50",
        "rsi14",
        "adx14",
        "atr14",
        "adx_slope",
        "ema14_slope_percent",
        "ema50_slope_percent",
    ):
        op.add_column("signals", sa.Column(column_name, sa.Numeric(36, 12), nullable=True))


def downgrade() -> None:
    """移除 Signal 技术指标快照字段。"""
    for column_name in reversed((
        "ema14",
        "ema50",
        "rsi14",
        "adx14",
        "atr14",
        "adx_slope",
        "ema14_slope_percent",
        "ema50_slope_percent",
    )):
        op.drop_column("signals", column_name)
