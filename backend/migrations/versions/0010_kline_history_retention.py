"""将每个交易对周期的数据库 K 线限制为最新 498 根。"""

from alembic import op
import sqlalchemy as sa


revision = "0010_kline_history_retention"
down_revision = "0009_active_pool_entry_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除每个交易对周期第 499 根及更早的 K 线，释放数据供 PostgreSQL 回收。"""
    op.execute(sa.text("""
        WITH ranked_klines AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol, timeframe
                    ORDER BY open_time DESC, id DESC
                ) AS retention_rank
            FROM klines
        )
        DELETE FROM klines
        USING ranked_klines
        WHERE klines.id = ranked_klines.id
          AND ranked_klines.retention_rank > 498
    """))


def downgrade() -> None:
    """历史 K 线删除后无法恢复，降级无需执行结构变更。"""
    pass
