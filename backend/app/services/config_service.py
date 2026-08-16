"""动态扫描配置的数据库读取与更新服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemConfig
from app.schemas import ConfigUpdate, ConfigValues

CONFIG_KEY = "scanner"


class ConfigService:
    """以单一 JSON 文档维护可在线调整的扫描参数。"""

    async def get(self, session: AsyncSession) -> ConfigValues:
        """读取配置；首次运行时返回经过校验的默认值。"""
        row = await session.get(SystemConfig, CONFIG_KEY)
        return ConfigValues.model_validate_json(row.value) if row else ConfigValues()

    async def update(self, session: AsyncSession, changes: ConfigUpdate) -> ConfigValues:
        """合并局部修改并原子提交完整配置。"""
        current = await self.get(session)
        values = current.model_copy(update=changes.model_dump(exclude_none=True))
        row = await session.get(SystemConfig, CONFIG_KEY)
        payload = values.model_dump_json()
        if row:
            row.value = payload
        else:
            session.add(SystemConfig(key=CONFIG_KEY, value=payload, description="Scanner runtime configuration"))
        await session.commit()
        return values
