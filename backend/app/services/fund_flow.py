"""合约主动资金流与 Open Interest 联合分析服务。"""

from bisect import bisect_right
from datetime import datetime, timedelta
from decimal import Decimal

from app.schemas import (
    ContractFundFlowData,
    ContractFundFlowPoint,
    ContractFundFlowSummary,
    FundFlowKline,
    KlineData,
    OIPoint,
    SignalFundFlowSnapshot,
)


ZERO = Decimal("0")
HUNDRED = Decimal("100")
OI_PERIOD_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}
# Binance 仅提供最近约一个月的 OI 历史，预留一天余量避免边界时刻被判定为过期。
OI_HISTORY_SAFE_WINDOW = timedelta(days=29)


def clamp_oi_history_start_ms(now_ms: int, requested_start_ms: int) -> int:
    """把 OI 查询起点限制在 Binance 可用历史窗口内，同时保留较短周期的原始范围。"""
    earliest_safe_ms = now_ms - int(OI_HISTORY_SAFE_WINDOW.total_seconds() * 1000)
    return max(requested_start_ms, earliest_safe_ms)


def percent_change(current: Decimal, previous: Decimal | None) -> Decimal | None:
    """计算相对变化百分比；缺少基准或基准为零时不伪造变化率。"""
    if previous is None or previous == ZERO:
        return None
    return (current - previous) / previous * HUNDRED


def classify_regime(
    price_change_percent: Decimal | None,
    open_interest_change: Decimal | None,
    net_taker_flow: Decimal,
) -> str:
    """结合价格、OI 和主动成交方向识别开仓或平仓主导状态。"""
    if price_change_percent is None or open_interest_change is None:
        return "insufficient_data"
    if open_interest_change > ZERO and price_change_percent > ZERO and net_taker_flow > ZERO:
        return "new_longs"
    if open_interest_change > ZERO and price_change_percent < ZERO and net_taker_flow < ZERO:
        return "new_shorts"
    if open_interest_change < ZERO and price_change_percent > ZERO and net_taker_flow > ZERO:
        return "short_covering"
    if open_interest_change < ZERO and price_change_percent < ZERO and net_taker_flow < ZERO:
        return "long_closing"
    return "mixed"


def build_signal_fund_flow_snapshot(
    current: KlineData,
    calculated_at: datetime,
    open_interest_change: Decimal,
    open_interest_change_percent: Decimal,
) -> SignalFundFlowSnapshot:
    """使用扫描时同一份未收盘 K 线和 OI 结果构建不可变 Signal 资金流快照。"""
    taker_sell = current.quote_volume - current.taker_buy_quote_volume
    net_flow = current.taker_buy_quote_volume - taker_sell
    taker_buy_ratio = (
        current.taker_buy_quote_volume / current.quote_volume * HUNDRED
        if current.quote_volume > ZERO else None
    )
    price_change = percent_change(current.close, current.open)
    return SignalFundFlowSnapshot(
        calculated_at=calculated_at,
        quote_volume=current.quote_volume,
        taker_buy_quote_volume=current.taker_buy_quote_volume,
        taker_sell_quote_volume=taker_sell,
        net_taker_flow=net_flow,
        taker_buy_ratio_percent=taker_buy_ratio,
        price_change_percent=price_change,
        open_interest_change=open_interest_change,
        open_interest_change_percent=open_interest_change_percent,
        regime=classify_regime(price_change, open_interest_change, net_flow),
    )


def build_contract_fund_flow(
    symbol: str,
    timeframe: str,
    klines: list[FundFlowKline],
    oi_points: list[OIPoint],
) -> ContractFundFlowData:
    """按 K 线收盘时间对齐最近 OI 观察点，并生成逐桶和窗口汇总数据。"""
    ordered_oi = sorted(oi_points, key=lambda item: item.timestamp)
    oi_timestamps = [item.timestamp for item in ordered_oi]
    points: list[ContractFundFlowPoint] = []
    previous_close: Decimal | None = None
    previous_oi: Decimal | None = None
    previous_oi_timestamp = None

    for kline in sorted(klines, key=lambda item: item.open_time):
        # OI 接口按固定周期采样，取不晚于 K 线结束时刻的最新值以避免使用未来数据。
        oi_index = bisect_right(oi_timestamps, kline.close_time) - 1
        oi = ordered_oi[oi_index] if oi_index >= 0 else None
        current_oi = oi.open_interest if oi else None
        has_new_oi_sample = oi is not None and oi.timestamp != previous_oi_timestamp
        oi_change = (
            current_oi - previous_oi
            if has_new_oi_sample and current_oi is not None and previous_oi is not None
            else None
        )
        oi_change_percent = (
            percent_change(current_oi, previous_oi)
            if has_new_oi_sample and current_oi is not None
            else None
        )
        price_change = percent_change(kline.close, previous_close)
        taker_sell = kline.quote_volume - kline.taker_buy_quote_volume
        net_flow = kline.taker_buy_quote_volume - taker_sell
        points.append(ContractFundFlowPoint(
            time=int(kline.open_time.timestamp()),
            close=kline.close,
            quote_volume=kline.quote_volume,
            taker_buy_quote_volume=kline.taker_buy_quote_volume,
            taker_sell_quote_volume=taker_sell,
            net_taker_flow=net_flow,
            price_change_percent=price_change,
            open_interest=current_oi,
            open_interest_value=oi.open_interest_value if oi else None,
            open_interest_change=oi_change,
            open_interest_change_percent=oi_change_percent,
            regime=classify_regime(price_change, oi_change, net_flow),
        ))
        previous_close = kline.close
        # 同一个 OI 观察点可能覆盖较细 K 线，重复点不能被误判为持仓没有变化。
        if has_new_oi_sample and current_oi is not None:
            previous_oi = current_oi
            previous_oi_timestamp = oi.timestamp

    oi_covered_points = [point for point in points if point.open_interest is not None]
    first_oi = oi_covered_points[0].open_interest if oi_covered_points else None
    last_oi = oi_covered_points[-1].open_interest if oi_covered_points else None
    summary_oi_change = last_oi - first_oi if first_oi is not None and last_oi is not None else None
    summary_oi_percent = percent_change(last_oi, first_oi) if last_oi is not None else None
    # 长周期 K 线可能早于 Binance 的 OI 保留期，汇总必须使用价格、资金流与 OI 的共同窗口。
    summary_points = oi_covered_points or points
    summary_price_percent = (
        percent_change(summary_points[-1].close, summary_points[0].close)
        if len(summary_points) >= 2 else None
    )
    summary_net_flow = sum((point.net_taker_flow for point in summary_points), ZERO)
    summary = ContractFundFlowSummary(
        net_taker_flow=summary_net_flow,
        price_change_percent=summary_price_percent,
        open_interest_change=summary_oi_change,
        open_interest_change_percent=summary_oi_percent,
        regime=classify_regime(summary_price_percent, summary_oi_change, summary_net_flow),
    )
    return ContractFundFlowData(symbol=symbol, timeframe=timeframe, points=points, summary=summary)
