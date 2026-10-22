from decimal import Decimal

import pytest

from pybynix import (
    BollingerBandsResult,
    Candle,
    MACDResult,
    StochasticResult,
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    stochastic,
)


def candles(closes):
    return [
        Candle(
            index * 60,
            Decimal(str(close - 1)),
            Decimal(str(close + 1)),
            Decimal(str(close - 2)),
            Decimal(str(close)),
            Decimal("1"),
        )
        for index, close in enumerate(closes)
    ]


def test_sma_and_ema_are_decimal_and_position_aligned():
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]

    assert sma(values, 2) == [None, Decimal("1.5"), Decimal("2.5"), Decimal("3.5")]
    assert ema(values, 2) == [None, Decimal("1.5"), Decimal("2.5"), Decimal("3.5")]
    assert sma(candles([1, 2, 3]), 2) == [None, Decimal("1.5"), Decimal("2.5")]


def test_rsi_bollinger_macd_and_result_types():
    values = [Decimal(str(value)) for value in range(1, 8)]

    assert rsi(values, 3)[3:] == [Decimal("100")] * 4
    bands = bollinger_bands(values, 3, multiplier=1)
    assert isinstance(bands, BollingerBandsResult)
    assert bands.middle[2] == Decimal("2")
    assert bands.upper[2] == Decimal("2") + (Decimal(2) / Decimal(3)).sqrt()
    result = macd(values, fast_period=2, slow_period=3, signal_period=2)
    assert isinstance(result, MACDResult)
    assert len(result.macd) == len(values) == len(result.signal) == len(result.histogram)
    assert result.macd[:2] == [None, None]
    assert result.signal[2] is None
    assert result.signal[3] is not None
    assert result.histogram[4] is not None
    assert result.macd[4] is not None
    assert result.signal[4] is not None
    assert result.histogram[4] == result.macd[4] - result.signal[4]


def test_atr_and_stochastic_use_candle_ohlc_and_flat_ranges_are_defined():
    data = candles([10, 11, 12, 13])
    ranges = atr(data, period=2)
    assert ranges[:1] == [None]
    assert ranges[1] == Decimal("3")
    stochastic_result = stochastic(data, period=2, smooth_period=2)
    assert isinstance(stochastic_result, StochasticResult)
    assert stochastic_result.k[1:] == [Decimal("75"), Decimal("75"), Decimal("75")]
    assert stochastic_result.d[0:2] == [None, None]
    assert stochastic_result.d[2:] == [Decimal("75"), Decimal("75")]


@pytest.mark.parametrize("function", [sma, ema, rsi, bollinger_bands])
def test_price_indicators_reject_invalid_period(function):
    with pytest.raises(ValueError):
        function([Decimal("1")], 0)
