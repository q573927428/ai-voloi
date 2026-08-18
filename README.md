# Binance VolOI Surveillance

一个面向 Binance USDⓈ-M USDT 永续合约的异步监控平台，覆盖标准永续及美股、ETF、贵金属、能源等 TradFi 永续。系统通过 REST 初始化历史 K 线、WebSocket 实时维护当前 K 线，并严格对齐时间边界扫描“预计成交量异常 + OI 增长”组合信号。

## 项目结构

```text
backend/                    FastAPI 异步后端
  app/api/                  HTTP / WebSocket API
  app/core/                 配置与数据库
  app/models/               SQLAlchemy 表模型
  app/schemas/              Pydantic 数据结构
  app/services/binance/     Binance REST 客户端
  app/services/cache/       内存 K 线缓存与指标
  app/services/websocket/   实时 K 线连接管理
  app/services/scanner/     Volume + OI 扫描器
  app/services/performance/ 未来表现回填
  migrations/               Alembic 数据库迁移
  tests/                    单元测试
frontend/                   Vue 3 + TypeScript 管理界面
docker-compose.yml          PostgreSQL、后端、前端编排
```

## 数据流程与 Binance API

启动时调用一次 `GET /fapi/v1/exchangeInfo` 获取 `TRADING + PERPETUAL + USDT` 合约，并分别用一次 `GET /fapi/v1/ticker/24hr` 和 `GET /fapi/v1/premiumIndex` 批量取得全市场 ticker 与最近一期资金费率。达到 `min_24h_quote_volume` 的交易对进入动态池。

新入池交易对通过 `GET /fapi/v1/klines` 初始化每周期 498 根历史 K 线。完整历史在内存中按市场保存为固定容量的列式 Float64 环形数组，数据库也按交易对和周期仅保留最新 498 根；交易对退出活跃池或周期被禁用时立即释放对应缓存。常态运行订阅 `<symbol>@kline_<timeframe>` 组合流，不会在每次扫描时重新请求所有 K 线。连接带心跳、断线指数退避和自动重新订阅；检测到收盘时间缺口后，REST 拉取最近窗口补齐。

只有成交量条件通过的 `symbol + timeframe` 才调用 `GET /futures/data/openInterestHist` 获取回看起点，并调用 `GET /fapi/v1/openInterest` 获取检测时刻的实时 OI；同轮扫描中相同交易对的实时结果由多个周期复用。统一客户端包含速率限制、并发信号量、超时，以及针对 `429/500/502/503` 和网络异常的有限指数退避。市场池快照每 15 分钟批量刷新一次 ticker 与资金费率。

## Scanner 逻辑

扫描器按服务器 UTC 时间计算下一个 `scan_interval_minutes` 边界，默认在 `00/05/10/...` 分执行：

1. 从内存缓存读取当前 K 线和完整历史 K 线。
2. `progress = elapsed / duration`；低于 `min_progress_percent` 直接跳过。
3. `estimatedVolume = currentVolume / progress`。
4. 用最近 `volume_ema_period` 根已收盘 K 线计算 EMA，当前 K 线不进入基准。
5. `volumeRatio = estimatedVolume / volumeEMA`；达到阈值后才请求 OI。
6. 按 `oi_lookback_minutes_by_timeframe` 为每个 K 线周期独立确定 OI 回看窗口；默认 `15m/30m/1h/4h/1d` 分别回看 `15/30/60/240/1440` 分钟，并自动选择可在 100 个观察点内覆盖窗口的最细采样粒度。
7. `oiChangePercent = (newestOI - oldestOI) / oldestOI × 100`。默认阈值 `0.05` 表示 `0.05%`。
8. 两项条件同时满足时保存 `VOLUME_OI_ANOMALY` 完整快照，并推送前端；同一 `symbol + timeframe + open_time` 只生成一次。

每个 Signal 还会保存对应周期最近完整 K 线计算出的版本化技术指标快照。趋势类包括 `EMA9/14/21/50/100/200`、价格距离、百分比斜率、EMA 排列、Wilder `ADX14/+DI14/-DI14`；动量类包括 `RSI14`、`MACD(12,26,9)`；波动率类包括 `ATR14/ATR%`、`Bollinger Bands(20,2)`；量价类包括 `MFI14` 和以当前历史窗口首根为零点的 `OBV`。当前未完成 K 线不参与这些指标，避免后续回测出现指标重绘。固定指标在每根 K 线收盘时增量推进，扫描器直接读取最新不可变计算结果，不再为每轮扫描展开并遍历全部历史；历史覆盖修正时才从当前窗口重建状态。

从 `0001` 升级且已有 Signal 时，可在迁移后执行 `python -m scripts.backfill_signal_indicators`，使用数据库中的历史完整 K 线补齐旧快照指标。

每次扫描都会写入 `scanner_runs`，包括交易对数、候选数、OI 请求数、Signal 数量、耗时与错误摘要。单个 OI 请求失败只增加错误计数，不中断整批扫描。

## 数据库表

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `symbols` | 合约、活跃状态及 24h 市场数据 | `symbol` 主键 |
| `klines` | 完整历史 K 线 | `symbol + timeframe + open_time` 唯一 |
| `open_interest_snapshots` | Signal 候选的 OI 观察点 | 交易对/周期/时间索引 |
| `signals` | 检测时刻不可变完整快照 | UUID 主键；业务不提供修改 API |
| `signal_future_performance` | 5m 至 2d 历史时点价格变化及观察点最大涨跌幅 | `signal_id` 一对一 |
| `scanner_runs` | 每次扫描审计数据 | 扫描时间索引 |
| `system_config` | 动态运行参数 | `key` 主键 |

迁移入口为 `backend/migrations/versions/0001_initial.py`。容器启动时自动执行 `alembic upgrade head`。

## API 列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | API、采集器、Binance WebSocket 状态 |
| `GET` | `/api/dashboard` | 交易池、扫描和今日 Signal 指标 |
| `GET` | `/api/markets/all` | 全部 USDT 永续合约及 24h 行情快照 |
| `GET` | `/api/markets/active` | 活跃交易池及最近一次 24h 行情快照 |
| `GET` | `/api/signals` | Signal 分页、搜索、周期筛选和排序 |
| `GET` | `/api/signals/{id}` | 完整快照及未来表现 |
| `GET` | `/api/markets/{symbol}/{timeframe}/indicators` | 最新或指定截止时间的结构化技术指标 |
| `GET` | `/api/config` | 当前动态配置 |
| `PATCH` | `/api/config` | 修改配置并触发交易池刷新 |
| `POST` | `/api/scanner/run` | 手动执行扫描 |
| `WS` | `/api/ws/signals` | 新 Signal 实时推送 |

FastAPI 自动文档位于 `/docs`。

技术指标接口默认返回 `EMA9/14/21/50/100/200`，可通过逗号分隔的 `ema` 参数请求最多 12 个 `2-500` 周期，也可用 ISO 8601 `at` 参数获取不晚于该时间的历史指标：

```text
GET /api/markets/BTCUSDT/15m/indicators?ema=9,21,50,100,200&at=2026-08-17T08:00:00Z
```

响应包含 `as_of`、`candle_count`、`closed_candles_only`、`version` 和 `warmup_complete` 等口径元数据。`warmup_complete` 要求历史数量至少达到本次最长 EMA 周期的两倍；数据不足以计算完整基础指标集时返回 `422`。

## 前端页面

- 运行概览：合约总数、活跃池、WebSocket 状态、最后扫描、耗时、今日 Signal 和最近列表。
- Signal 监控：交易对搜索、周期筛选、检测时间/Volume Ratio/OI 排序与分页。
- Signal 详情：K 线、六组价格 EMA、EMA 距离与排列、ADX 方向、ATR%、成交量、OI、24h 行情、触发参数和未来表现完整快照。
- 参数配置：核心阈值在线校验和修改，保存后动态刷新交易池。

## 配置参数

动态业务配置保存在 `system_config`：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `timeframes` | `15m,30m,1h,4h,1d` | 监控周期 |
| `min_24h_quote_volume` | `10000000` | 活跃池最低 24h USDT 成交额 |
| `volume_ema_period` | `12` | 完整 K 线 EMA 周期 |
| `volume_multiplier` | `1.5` | 成交量异常倍数 |
| `min_progress_percent` | `10` | 最小 K 线进度百分比 |
| `oi_lookback_minutes_by_timeframe` | 按周期设置 | 各 K 线周期独立的 OI 回看分钟数 |
| `oi_change_threshold_percent_by_timeframe` | 按周期设置，默认均为 `0.05` | 各 K 线周期独立的 OI 变化百分比阈值 |
| `scan_interval_minutes` | `5` | 边界扫描间隔 |

网络、数据库、并发和缓存参数通过 `.env` 配置，完整示例见 `.env.example`。

## 启动方式

推荐使用 Docker Compose（需要 Docker Desktop）：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

浏览器访问 `http://localhost:5173`，API 为 `http://localhost:8000`。

本地开发不需要 Docker，需要 Python 3.12+、Node.js 22+ 和 PostgreSQL 16+。先确认 Windows PostgreSQL 服务和端口：

```powershell
Get-Service postgresql*
Start-Service postgresql-x64-18  # 服务名以本机实际安装版本为准
Test-NetConnection localhost -Port 5432
```

复制环境示例并修改 `DATABASE_URL` 中的用户、密码和数据库名：

```powershell
Copy-Item .env.example .env
# 示例：DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/postgres
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload
```

另一个终端：

```powershell
cd frontend
npm install
npm run dev
```

## 测试与构建

```powershell
cd backend
pytest

cd ..\frontend
npm run build
```

单元测试覆盖 EMA 仅使用完整 K 线、EMA 数据量限制、K 线进度钳制、OI 时间戳匹配及配置边界。生产环境还应为 Binance 网络故障、PostgreSQL 备份恢复和长时间 WebSocket 重连配置外部集成测试与监控。

## 异常与一致性保证

- REST 请求通过全局速率限制和并发限制排队，不会为全市场组合一次性创建无限任务。
- `429` 尊重 `Retry-After`，服务端错误和网络错误指数退避，达到上限后明确失败。
- WebSocket 使用心跳、超时、自动重连和 REST 缺口修复，单连接流数量可配置并自动分片。
- K 线使用数据库唯一约束和应用层更新保证幂等。
- Signal 保存全部阈值及市场字段，生成后无更新/删除 API，确保回测快照不变。
- Binance 临时不可达时后台任务持续退避重试，API 健康状态会报告采集阶段；数据库不可用时启动明确失败，避免以假健康状态运行。
