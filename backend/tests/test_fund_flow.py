"""合约资金流与 Open Interest 联合分析测试。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas import FundFlowKline, OIPoint
from app.services.fund_flow import build_contract_fund_flow, classify_regime


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
