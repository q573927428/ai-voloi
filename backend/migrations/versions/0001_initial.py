"""创建 VolOI 全部核心数据表。"""

from alembic import op

from app.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按模型元数据创建第一版表结构及约束。"""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """逆序删除第一版表结构。"""
    Base.metadata.drop_all(bind=op.get_bind())
