"""HTTP 与 WebSocket API 路由。"""

from datetime import datetime, time, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import Kline, ScannerRun, Signal, Symbol
from app.schemas import (
    ConfigUpdate,
    ConfigValues,
    DashboardStats,
    SignalChartCandle,
    SignalChartData,
    SignalPage,
    SignalRead,
)
from app.services.cache.technical_indicators import ema_series

router = APIRouter()


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
    day_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    today = await session.scalar(select(func.count()).select_from(Signal).where(Signal.detected_at >= day_start)) or 0
    return DashboardStats(
        total_symbols=total, active_symbols=active,
        websocket_status=request.app.state.runtime.websocket.status,
        last_scan_at=last_run.completed_at if last_run else None,
        last_scan_duration_ms=last_run.duration_ms if last_run else None,
        today_signal_count=today, current_signal_count=len(request.app.state.broadcaster.connections),
    )


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
    """分页检索不可变 Signal 快照，支持页面要求的筛选和排序。"""
    filters = []
    if symbol:
        filters.append(Signal.symbol.ilike(f"%{symbol}%"))
    if timeframe:
        filters.append(Signal.timeframe == timeframe)
    total = await session.scalar(select(func.count()).select_from(Signal).where(*filters)) or 0
    column = getattr(Signal, sort_by)
    ordering = asc(column) if sort_order == "asc" else desc(column)
    rows = (await session.execute(
        select(Signal).where(*filters).order_by(ordering).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return SignalPage(items=[SignalRead.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/signals/{signal_id}")
async def signal_detail(signal_id: UUID, session: AsyncSession = Depends(get_db)) -> dict:
    """返回 Signal 完整快照及未来表现。"""
    signal = (await session.execute(
        select(Signal).options(selectinload(Signal.future_performance)).where(Signal.id == signal_id)
    )).scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    snapshot = SignalRead.model_validate(signal).model_dump(mode="json")
    performance = signal.future_performance
    snapshot["future_performance"] = {
        key: str(getattr(performance, key)) if getattr(performance, key) is not None else None
        for key in ("return_5m", "return_15m", "return_30m", "return_1h", "return_4h", "return_1d", "max_profit_percent", "max_loss_percent")
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
    closes = [row.close for row in rows]
    ema14_values = ema_series(closes, 14)
    ema50_values = ema_series(closes, 50)
    candles: list[SignalChartCandle] = []
    for index, row in enumerate(rows):
        ema14_index = index - 13
        ema50_index = index - 49
        candles.append(SignalChartCandle(
            time=int(row.open_time.timestamp()),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            ema14=ema14_values[ema14_index] if ema14_index >= 0 else None,
            ema50=ema50_values[ema50_index] if ema50_index >= 0 else None,
        ))
    # 当前未完成 K 线使用 Signal 原始快照，EMA 使用检测时已保存值，保持回测口径一致。
    candles.append(SignalChartCandle(
        time=int(signal.open_time.timestamp()),
        open=signal.open,
        high=signal.high,
        low=signal.low,
        close=signal.current_price,
        volume=signal.current_volume,
        ema14=signal.ema14,
        ema50=signal.ema50,
        is_signal=True,
    ))
    return SignalChartData(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        signal_time=int(signal.detected_at.timestamp()),
        signal_open_time=int(signal.open_time.timestamp()),
        candles=candles,
    )


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
        run = await runtime.scanner.scan(runtime.active_symbols, runtime.tickers, runtime.config)
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
