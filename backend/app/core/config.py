"""应用环境变量与静态基础配置。"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """进程级配置，承载数据库、Binance 网络和并发参数。"""
    app_name: str = "Binance VolOI Surveillance"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+asyncpg://voloi:voloi@localhost:5432/voloi"
    binance_rest_url: str = "https://fapi.binance.com"
    binance_ws_url: str = "wss://fstream.binance.com/stream"
    allowed_origins: list[str] = ["http://localhost:5173"]
    auto_start_monitor: bool = True
    # 1000 根 K 线请求的 Binance 权重较高，保留足够余量给 OI、ticker 和重试请求。
    rest_rate_per_second: float = Field(default=4, gt=0)
    rest_concurrency: int = Field(default=5, ge=1, le=20)
    rest_timeout_seconds: float = Field(default=10, gt=0)
    rest_max_retries: int = Field(default=3, ge=0, le=10)
    ws_streams_per_connection: int = Field(default=150, ge=1, le=200)
    kline_history_limit: int = Field(default=1000, ge=20, le=1000)

    # 从项目根目录或 backend 目录启动都能读取本地配置；后加载的 backend/.env 可作开发覆盖。
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置实例。"""
    return Settings()
