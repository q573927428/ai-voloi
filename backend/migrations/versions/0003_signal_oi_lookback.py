"""为 Signal 快照增加固定 OI 观察窗口。"""

import sqlalchemy as sa
from alembic import op

revision = "0003_signal_oi_lookback"
down_revision = "0002_signal_technical_indicators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """记录新口径的 OI 观察分钟数；历史记录保留 NULL 表示旧口径。"""
    op.add_column("signals", sa.Column("oi_lookback_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    """移除 OI 观察窗口快照字段。"""
    op.drop_column("signals", "oi_lookback_minutes")
