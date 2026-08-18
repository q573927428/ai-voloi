"""HTTP 与 WebSocket API 路由。"""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import asc, case, desc, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import Kline, ScannerRun, Signal, Symbol
from app.schemas import (
    ActiveSymbolRead,
    ContractFundFlowData,
    ConfigUpdate,
    ConfigValues,
    DashboardStats,
    KlineData,
    MarketIndicatorsRead,
    RealtimeChartData,
    SignalChartCandle,
    SignalChartData,
    SignalListRead,
    SignalPage,
    SignalRead,
)
from app.services.cache.technical_indicators import (
    DEFAULT_EMA_PERIODS,
    build_indicator_response,
    calculate_technical_indicators,
)
from app.services.chart import build_chart_candles, latest_ema_values
from app.services.fund_flow import (
    OI_PERIOD_SECONDS,
    build_contract_fund_flow,
    clamp_oi_history_start_ms,
)

router = APIRouter()
logger = logging.getLogger(__name__)
UTC_PLUS_8 = timezone(timedelta(hours=8))


def utc_plus_8_day_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    """返回当前 UTC+8 自然日在 UTC 中的左闭右开时间范围。"""
    current = now or datetime.now(timezone.utc)
    local_date = current.astimezone(UTC_PLUS_8).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=UTC_PLUS_8)
    # 数据库存储 UTC，查询边界也转换为 UTC，避免改变全局时间存储约定。
    start = local_start.astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def parse_ema_periods(value: str) -> tuple[int, ...]:
    """解析逗号分隔的 EMA 周期，限制数量与范围以控制接口计算成本。"""
    try:
        periods = tuple(
            dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip())
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="EMA periods must be comma-separated integers") from exc
    if not periods:
        raise HTTPException(status_code=422, detail="At least one EMA period is required")
    if len(periods) > 12:
        raise HTTPException(status_code=422, detail="At most 12 EMA periods are allowed")
    if any(period < 2 or period > 500 for period in periods):
        raise HTTPException(status_code=422, detail="EMA periods must be between 2 and 500")
    return periods


@router.get("/health")
async def health(request: Request) -> dict:
    """返回 API 与采集器健康状态。"""
    runtime = request.app.state.runtime
    return {"status": "ok", "collector": runtime.initialization_status, "websocket": runtime.websocket.status}


@router.get("/dashboard", response_model=DashboardStats)
async def dashboard(request: Request, session: AsyncSession = Depends(get_db)) -> DashboardStats:
    """汇总仪表盘所需的核心运行指标。"""
    total = await session.scalar(select(func.count()).select_from(Symbol)) or 0
    active = await session.scalar(select(func.count()).select_from(Symbol).where(Symbol.is_active)) or 0
    last_run = (await session.execute(select(ScannerRun).order_by(ScannerRun.started_at.desc()).limit(1))).scalar_one_or_none()
    day_start, day_end = utc_plus_8_day_range()
    today = await session.scalar(
        select(func.count()).select_from(Signal).where(
            Signal.detected_at >= day_start,
            Signal.detected_at < day_end,
        )
    ) or 0
    return DashboardStats(
        total_symbols=total, active_symbols=active,
        websocket_status=request.app.state.runtime.websocket.status,
        last_scan_at=last_run.completed_at if last_run else None,
        last_scan_duration_ms=last_run.duration_ms if last_run else None,
        today_signal_count=today, current_signal_count=len(request.app.state.broadcaster.connections),
    )


@router.get("/markets/active", response_model=list[ActiveSymbolRead])
async def active_markets(session: AsyncSession = Depends(get_db)) -> list[ActiveSymbolRead]:
    """返回活跃交易池及其最近一次 24h 行情，供仪表盘查看完整明细。"""
    rows = (await session.execute(
        select(Symbol)
        .where(Symbol.is_active.is_(True))
        .order_by(Symbol.quote_volume_24h.desc().nullslast(), Symbol.symbol.asc())
    )).scalars().all()
    return [ActiveSymbolRead.model_validate(row) for row in rows]


@router.get("/markets/all", response_model=list[ActiveSymbolRead])
async def all_markets(session: AsyncSession = Depends(get_db)) -> list[ActiveSymbolRead]:
    """返回全部 USDT 永续合约及最近一次 24h 行情，供仪表盘查看完整明细。"""
    rows = (await session.execute(
        select(Symbol)
        .order_by(Symbol.quote_volume_24h.desc().nullslast(), Symbol.symbol.asc())
    )).scalars().all()
    return [ActiveSymbolRead.model_validate(row) for row in rows]


@router.get("/signals", response_model=SignalPage)
async def list_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    sort_by: str = Query("detected_at", pattern="^(detected_at|volume_ratio|oi_change_percent)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> SignalPage:
    """按共振组分页检索 Signal，确保同交易对、同检测时间的多周期明细不跨页。"""
    filters = []
    if symbol:
        filters.append(Signal.symbol.ilike(f"%{symbol}%"))
    if timeframe:
        filters.append(Signal.timeframe == timeframe)
    # total 保留周期明细总数，group_total 才是页面分页所用的共振组总数。
    total = await session.scalar(select(func.count()).select_from(Signal).where(*filters)) or 0
    grouped_signals = (
        select(Signal.symbol, Signal.detected_at)
        .where(*filters)
        .group_by(Signal.symbol, Signal.detected_at)
        .subquery()
    )
    group_total = await session.scalar(select(func.count()).select_from(grouped_signals)) or 0

    column = getattr(Signal, sort_by)
    group_sort_value = func.max(column).label("sort_value")
    group_size = func.count(Signal.id).label("group_size")
    longest_timeframe = func.max(case(
        (Signal.timeframe == "15m", 15),
        (Signal.timeframe == "30m", 30),
        (Signal.timeframe == "1h", 60),
        (Signal.timeframe == "4h", 240),
        (Signal.timeframe == "1d", 1440),
        else_=0,
    )).label("longest_timeframe")
    group_ordering = asc(group_sort_value) if sort_order == "asc" else desc(group_sort_value)
    group_query = (
        select(
            Signal.symbol,
            Signal.detected_at,
            group_sort_value,
            group_size,
            longest_timeframe,
        )
        .where(*filters)
        .group_by(Signal.symbol, Signal.detected_at)
    )
    # 按时间查看时，同轮扫描优先展示共振周期更多、最长周期更长的组。
    if sort_by == "detected_at":
        group_query = group_query.order_by(
            group_ordering,
            desc(group_size),
            desc(longest_timeframe),
            Signal.symbol.asc(),
        )
    else:
        # 指标排序以组内最大值代表该共振组，再用组键保证结果稳定。
        group_query = group_query.order_by(
            group_ordering,
            Signal.detected_at.desc(),
            Signal.symbol.asc(),
        )
    selected_groups = (await session.execute(
        group_query.offset((page - 1) * page_size).limit(page_size)
    )).all()

    if not selected_groups:
        return SignalPage(
            items=[], total=total, group_total=group_total, page=page, page_size=page_size
        )

    group_keys = [(row.symbol, row.detected_at) for row in selected_groups]
    rows = (await session.execute(
        select(Signal, Symbol.contract_type)
        .options(selectinload(Signal.future_performance))
        .join(Symbol, Symbol.symbol == Signal.symbol)
        .where(*filters, tuple_(Signal.symbol, Signal.detected_at).in_(group_keys))
        .order_by(Signal.symbol.asc(), Signal.detected_at.desc(), Signal.timeframe.asc())
    )).all()

    rows_by_group: dict[tuple[str, datetime], list[tuple[Signal, str]]] = {}
    for signal, contract_type in rows:
        rows_by_group.setdefault((signal.symbol, signal.detected_at), []).append((signal, contract_type))

    # 按已分页的组顺序展开明细，不让第二次查询的数据库顺序破坏组排名。
    items: list[SignalListRead] = []
    for group in selected_groups:
        for signal, contract_type in rows_by_group.get((group.symbol, group.detected_at), []):
            items.append(SignalListRead.model_validate(signal).model_copy(
                update={"is_tradfi": contract_type == "TRADIFI_PERPETUAL"}
            ))
    return SignalPage(
        items=items,
        total=total,
        group_total=group_total,
        page=page,
        page_size=page_size,
    )


@router.get("/signals/{signal_id}")
async def signal_detail(signal_id: UUID, session: AsyncSession = Depends(get_db)) -> dict:
    """返回 Signal 完整快照及未来表现。"""
    signal = (await session.execute(
        select(Signal).options(selectinload(Signal.future_performance)).where(Signal.id == signal_id)
    )).scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    contract_type = await session.scalar(
        select(Symbol.contract_type).where(Symbol.symbol == signal.symbol)
    )
    snapshot = SignalRead.model_validate(signal).model_copy(
        update={"is_tradfi": contract_type == "TRADIFI_PERPETUAL"}
    ).model_dump(mode="json")
    performance = signal.future_performance
    snapshot["future_performance"] = {
        key: str(getattr(performance, key)) if getattr(performance, key) is not None else None
        for key in (
            "price_change_5m_percent", "price_change_15m_percent",
            "price_change_30m_percent", "price_change_1h_percent",
            "price_change_4h_percent", "price_change_8h_percent",
            "price_change_12h_percent", "price_change_16h_percent",
            "price_change_1d_percent", "price_change_2d_percent",
            "max_rise_percent", "max_drop_percent",
        )
    } if performance else None
    return snapshot


@router.get("/signals/{signal_id}/chart", response_model=SignalChartData)
async def signal_chart(
    signal_id: UUID,
    history_limit: int = Query(1000, ge=50, le=1000),
    session: AsyncSession = Depends(get_db),
) -> SignalChartData:
    """返回 Signal 之前的完整 K 线和检测时刻快照，不包含未来价格。"""
    signal = await session.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    rows = (await session.execute(
        select(Kline)
        .where(
            Kline.symbol == signal.symbol,
            Kline.timeframe == signal.timeframe,
            Kline.is_closed.is_(True),
            Kline.open_time < signal.open_time,
        )
        .order_by(Kline.open_time.desc())
        .limit(history_limit)
    )).scalars().all()
    rows = list(reversed(rows))
    candles = build_chart_candles(rows)
    current_emas = latest_ema_values([*[row.close for row in rows], signal.current_price])
    # 当前未完成 K 线按 Signal 当时价格计算；历史固化的 EMA14/EMA50 优先保持原始口径。
    current_emas[14] = signal.ema14
    current_emas[50] = signal.ema50
    candles.append(SignalChartCandle(
        time=int(signal.open_time.timestamp()),
        open=signal.open,
        high=signal.high,
        low=signal.low,
        close=signal.current_price,
        volume=signal.current_volume,
        emas=current_emas,
        is_signal=True,
    ))
    return SignalChartData(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        signal_time=int(signal.detected_at.timestamp()),
        signal_open_time=int(signal.open_time.timestamp()),
        candles=candles,
    )


@router.get("/markets/{symbol}/{timeframe}/chart", response_model=RealtimeChartData)
async def realtime_chart(
    symbol: str,
    timeframe: str,
    request: Request,
    history_limit: int = Query(1000, ge=50, le=1000),
    session: AsyncSession = Depends(get_db),
) -> RealtimeChartData:
    """返回指定市场的最新历史窗口和当前未收盘 K 线，作为实时模式初始状态。"""
    normalized_symbol = symbol.upper()
    runtime = request.app.state.runtime
    if timeframe not in runtime.config.timeframes:
        raise HTTPException(status_code=404, detail="Unsupported timeframe")
    if runtime.websocket.is_stale(normalized_symbol, timeframe):
        try:
            # 首屏响应前修复最近窗口，避免数据库历史与当前缓存之间缺少已收盘 K 线。
            await runtime.repair_market_klines(normalized_symbol, timeframe)
        except Exception:
            # Binance 临时不可达时仍返回已有历史，实时兜底循环会在连接建立后继续重试。
            logger.exception("Failed to repair realtime chart gap for %s %s", normalized_symbol, timeframe)
    rows = (await session.execute(
        select(Kline)
        .where(
            Kline.symbol == normalized_symbol,
            Kline.timeframe == timeframe,
            Kline.is_closed.is_(True),
        )
        .order_by(Kline.open_time.desc())
        .limit(history_limit)
    )).scalars().all()
    rows = list(reversed(rows))
    current = await runtime.cache.current(normalized_symbol, timeframe)
    visible = [*rows, *([current] if current and (not rows or current.open_time > rows[-1].open_time) else [])]
    if not visible:
        raise HTTPException(status_code=404, detail="Market Kline data not found")
    return RealtimeChartData(
        symbol=normalized_symbol,
        timeframe=timeframe,
        candles=build_chart_candles(visible),
    )


@router.get("/markets/{symbol}/{timeframe}/fund-flow", response_model=ContractFundFlowData)
async def contract_fund_flow(
    symbol: str,
    timeframe: str,
    request: Request,
    limit: int = Query(120, ge=50, le=500),
) -> ContractFundFlowData:
    """返回主动买卖成交额、价格与 OI 变化对齐后的合约资金流窗口。"""
    normalized_symbol = symbol.upper()
    runtime = request.app.state.runtime
    if timeframe not in runtime.config.timeframes:
        raise HTTPException(status_code=404, detail="Unsupported timeframe")
    period_seconds = OI_PERIOD_SECONDS.get(timeframe)
    if period_seconds is None:
        raise HTTPException(status_code=422, detail="Open Interest does not support this timeframe")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    requested_start_ms = now_ms - period_seconds * (limit + 2) * 1000
    # K 线可继续返回完整窗口，但 OI 起点不能超过 Binance 约一个月的历史保留期。
    oi_start_ms = clamp_oi_history_start_ms(now_ms, requested_start_ms)
    try:
        klines, oi_points = await asyncio.gather(
            runtime.client.fund_flow_klines(normalized_symbol, timeframe, limit),
            runtime.client.open_interest(
                normalized_symbol,
                timeframe,
                oi_start_ms,
                limit=min(limit + 2, 500),
            ),
        )
    except Exception as exc:
        logger.exception("Failed to load contract fund flow for %s %s", normalized_symbol, timeframe)
        raise HTTPException(status_code=502, detail="Binance fund flow data is temporarily unavailable") from exc
    if not klines:
        raise HTTPException(status_code=404, detail="Market fund flow data not found")
    return build_contract_fund_flow(normalized_symbol, timeframe, klines, oi_points)


@router.get("/markets/{symbol}/{timeframe}/indicators", response_model=MarketIndicatorsRead)
async def market_indicators(
    symbol: str,
    timeframe: str,
    ema_periods: str = Query(
        default=",".join(str(period) for period in DEFAULT_EMA_PERIODS),
        alias="ema",
        description="Comma-separated EMA periods, each between 2 and 500",
    ),
    at: datetime | None = Query(
        default=None,
        description="Only use closed candles whose close time is not later than this timestamp",
    ),
    session: AsyncSession = Depends(get_db),
) -> MarketIndicatorsRead:
    """返回指定市场最新或历史截止时刻的完整技术指标。"""
    normalized_symbol = symbol.upper()
    periods = parse_ema_periods(ema_periods)
    filters = [
        Kline.symbol == normalized_symbol,
        Kline.timeframe == timeframe,
        Kline.is_closed.is_(True),
    ]
    if at is not None:
        filters.append(Kline.close_time <= at)
    rows = (await session.execute(
        select(Kline)
        .where(*filters)
        .order_by(Kline.open_time.desc())
        .limit(1000)
    )).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Market Kline data not found")
    klines = [
        KlineData(
            symbol=row.symbol,
            timeframe=row.timeframe,
            open_time=row.open_time,
            close_time=row.close_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            quote_volume=row.quote_volume,
            taker_buy_quote_volume=row.taker_buy_quote_volume or 0,
            is_closed=row.is_closed,
        )
        for row in reversed(rows)
    ]
    indicators = calculate_technical_indicators(klines, periods)
    if indicators is None:
        raise HTTPException(
            status_code=422,
            detail="At least 51 closed candles are required for the complete indicator set",
        )
    return build_indicator_response(normalized_symbol, timeframe, indicators, periods)


@router.get("/config", response_model=ConfigValues)
async def get_config(session: AsyncSession = Depends(get_db)) -> ConfigValues:
    """读取当前扫描配置。"""
    from app.services.config_service import ConfigService
    return await ConfigService().get(session)


@router.patch("/config", response_model=ConfigValues)
async def update_config(changes: ConfigUpdate, request: Request, session: AsyncSession = Depends(get_db)) -> ConfigValues:
    """校验并持久化配置，然后立即刷新交易池。"""
    from app.services.config_service import ConfigService
    values = await ConfigService().update(session, changes)
    # 刷新涉及外部网络，放到后台避免配置接口长时间阻塞。
    import asyncio
    asyncio.create_task(request.app.state.runtime.apply_config(values))
    return values


@router.post("/scanner/run")
async def run_scanner(request: Request) -> dict:
    """手动触发一次扫描，主要用于运维验证。"""
    runtime = request.app.state.runtime
    if runtime.initialization_status != "ready":
        # 手动入口与定时任务采用同一完整性门槛，避免初始化中途写入不完整 Signal。
        raise HTTPException(status_code=503, detail="Collector is not ready")
    try:
        run = await runtime.scanner.scan(
            runtime.active_symbols,
            runtime.tickers,
            runtime.config,
            runtime.funding_rates,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": run.id, "signal_count": run.signal_count, "duration_ms": run.duration_ms}


@router.websocket("/ws/signals")
async def signal_stream(websocket: WebSocket) -> None:
    """向前端推送新 Signal，连接本身也承担存活检测。"""
    broadcaster = websocket.app.state.broadcaster
    await broadcaster.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await broadcaster.disconnect(websocket)


@router.websocket("/ws/klines/{symbol}/{timeframe}")
async def kline_stream(websocket: WebSocket, symbol: str, timeframe: str) -> None:
    """将既有 Binance 订阅收到的 K 线增量转发给对应实时图表。"""
    normalized_symbol = symbol.upper()
    runtime = websocket.app.state.runtime
    broadcaster = websocket.app.state.kline_broadcaster
    if normalized_symbol not in runtime.active_symbols or timeframe not in runtime.config.timeframes:
        await websocket.accept()
        await websocket.close(code=1008, reason="Market is not active")
        return
    await broadcaster.connect(websocket, normalized_symbol, timeframe)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await broadcaster.disconnect(websocket, normalized_symbol, timeframe)
