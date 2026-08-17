"""为 Signal 增加可扩展的结构化技术指标快照。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_signal_indicator_snapshot"
down_revision = "0005_symbol_funding_rate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加 JSONB 快照列，已有 Signal 由回填脚本按历史 K 线补齐。"""
    op.add_column(
        "signals",
        sa.Column("technical_indicators", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """移除结构化技术指标快照。"""
    op.drop_column("signals", "technical_indicators")
