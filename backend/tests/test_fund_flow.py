"""合约资金流与 Open Interest 联合分析测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import FundFlowKline, OIPoint
from app.services.fund_flow import (
    OI_HISTORY_SAFE_WINDOW,
    build_contract_fund_flow,
    clamp_oi_history_start_ms,
    classify_regime,
)


def test_clamp_oi_history_start_respects_binance_retention_window() -> None:
    """长周期查询不能把 OI 起点推到 Binance 历史保留期之外。"""
    now_ms = 2_000_000_000_000
    safe_window_ms = int(OI_HISTORY_SAFE_WINDOW.total_seconds() * 1000)
    recent_start_ms = now_ms - 7 * 24 * 60 * 60 * 1000

    assert clamp_oi_history_start_ms(now_ms, now_ms - safe_window_ms * 2) == (
        now_ms - safe_window_ms
    )
    assert clamp_oi_history_start_ms(now_ms, recent_start_ms) == recent_start_ms


def test_classify_regime_covers_open_and_close_directions() -> None:
    """价格、OI 和主动资金流的四个一致方向应得到明确市场状态。"""
    assert classify_regime(Decimal("1"), Decimal("10"), Decimal("100")) == "new_longs"
    assert classify_regime(Decimal("-1"), Decimal("10"), Decimal("-100")) == "new_shorts"
    assert classify_regime(Decimal("1"), Decimal("-10"), Decimal("100")) == "short_covering"
    assert classify_regime(Decimal("-1"), Decimal("-10"), Decimal("-100")) == "long_closing"
    assert classify_regime(Decimal("1"), Decimal("10"), Decimal("-100")) == "mixed"


def test_build_contract_fund_flow_aligns_oi_and_calculates_summary() -> None:
    """逐桶净主动流、OI 变化及窗口汇总必须使用同一时间口径。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    klines = [
        FundFlowKline(
            open_time=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            close=Decimal(100 + index * 2),
            quote_volume=Decimal("1000"),
            taker_buy_quote_volume=Decimal("700"),
        )
        for index in range(3)
    ]
    oi_points = [
        OIPoint(
            timestamp=start + timedelta(hours=index + 1),
            open_interest=Decimal(1000 + index * 100),
            open_interest_value=Decimal(100_000 + index * 12_000),
        )
        for index in range(3)
    ]

    result = build_contract_fund_flow("BTCUSDT", "1h", klines, oi_points)

    assert len(result.points) == 3
    assert result.points[0].net_taker_flow == Decimal("400")
    assert result.points[1].open_interest_change == Decimal("100")
    assert result.points[1].regime == "new_longs"
    assert result.summary.net_taker_flow == Decimal("1200")
    assert result.summary.open_interest_change == Decimal("200")
    assert result.summary.regime == "new_longs"


def test_summary_uses_only_kline_window_covered_by_open_interest() -> None:
    """超过 OI 保留期的旧 K 线仍可展示，但不能混入联合窗口汇总。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    closes = (Decimal("100"), Decimal("200"), Decimal("300"), Decimal("330"))
    taker_buys = (Decimal("100"), Decimal("200"), Decimal("700"), Decimal("800"))
    klines = [
        FundFlowKline(
            open_time=start + timedelta(days=index),
            close_time=start + timedelta(days=index + 1),
            close=closes[index],
            quote_volume=Decimal("1000"),
            taker_buy_quote_volume=taker_buys[index],
        )
        for index in range(4)
    ]
    oi_points = [
        OIPoint(
            timestamp=start + timedelta(days=index + 1),
            open_interest=Decimal(1000 + index * 100),
        )
        for index in (2, 3)
    ]

    result = build_contract_fund_flow("BTCUSDT", "1d", klines, oi_points)

    assert len(result.points) == 4
    assert result.points[0].open_interest is None
    assert result.summary.net_taker_flow == Decimal("1000")
    assert result.summary.price_change_percent == Decimal("10.0")
    assert result.summary.open_interest_change == Decimal("100")


def test_realtime_oi_only_completes_current_unfinished_kline() -> None:
    """实时 OI 应补齐当前柱变化，同时保持上一根已完成 K 线使用历史边界采样。"""
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    klines = [
        FundFlowKline(
            open_time=start + timedelta(minutes=index * 5),
            close_time=start + timedelta(minutes=(index + 1) * 5),
            close=Decimal(100 + index),
            quote_volume=Decimal("1000"),
            taker_buy_quote_volume=Decimal("600"),
        )
        for index in range(2)
    ]
    oi_points = [
        OIPoint(timestamp=start, open_interest=Decimal("900")),
        OIPoint(timestamp=start + timedelta(minutes=5), open_interest=Decimal("1000")),
        # 12:07 是实时接口时间，晚于已完成柱结束时间但早于当前柱结束时间。
        OIPoint(timestamp=start + timedelta(minutes=7), open_interest=Decimal("1050")),
    ]

    result = build_contract_fund_flow("BTCUSDT", "5m", klines, oi_points)

    assert result.points[0].open_interest == Decimal("1000")
    assert result.points[0].open_interest_change is None
    assert result.points[1].open_interest == Decimal("1050")
    assert result.points[1].open_interest_change == Decimal("50")
    assert result.points[1].open_interest_change_percent == Decimal("5.00")
