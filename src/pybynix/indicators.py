"""Pure Decimal technical indicators for caller-owned candle analysis.

The functions in this module consume an iterable of :class:`~pybynix.Candle`
objects or Decimal-like values and return position-aligned lists. Warm-up
positions that do not have enough observations are represented by ``None``;
the functions never emit trading signals or place orders.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias, cast

from .models import Candle

DecimalValue: TypeAlias = Decimal | None


@dataclass(frozen=True)
class MACDResult:
    """Aligned MACD line, signal line and histogram values."""

    macd: list[DecimalValue]
    signal: list[DecimalValue]
    histogram: list[DecimalValue]


@dataclass(frozen=True)
class BollingerBandsResult:
    """Aligned lower, middle (SMA) and upper Bollinger band values."""

    upper: list[DecimalValue]
    middle: list[DecimalValue]
    lower: list[DecimalValue]


@dataclass(frozen=True)
class StochasticResult:
    """Aligned stochastic %K and smoothed %D values."""

    k: list[DecimalValue]
    d: list[DecimalValue]


def _period(value, name="period"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _decimal(value, name="value"):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be Decimal-like")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be Decimal-like") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _values(values, *, field="close"):
    if field not in {"open", "high", "low", "close", "volume"}:
        raise ValueError("field must be open, high, low, close or volume")
    try:
        items = list(values)
    except TypeError:
        raise TypeError("values must be an iterable of Candle or Decimal-like values") from None
    result = []
    for item in items:
        value = getattr(item, field) if isinstance(item, Candle) else item
        result.append(_decimal(value, field))
    return result


def _candles(values):
    try:
        result = list(values)
    except TypeError:
        raise TypeError("candles must be an iterable of Candle objects") from None
    if not all(isinstance(candle, Candle) for candle in result):
        raise TypeError("candles must contain only Candle objects")
    return result


def _ema_values(values, period) -> list[DecimalValue]:
    result: list[DecimalValue] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = Decimal(2) / Decimal(period + 1)
    current = sum(values[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(values)):
        current = (values[index] - current) * alpha + current
        result[index] = current
    return result


def sma(values, period=14, *, field="close"):
    """Return a simple moving average aligned to ``values``.

    The first ``period - 1`` positions are ``None``. Candle inputs use the
    selected OHLCV ``field``; Decimal-like inputs ignore ``field``.
    """

    period = _period(period)
    data = _values(values, field=field)
    result: list[DecimalValue] = [None] * len(data)
    if len(data) < period:
        return result
    window = sum(data[:period], Decimal(0))
    result[period - 1] = window / Decimal(period)
    for index in range(period, len(data)):
        window += data[index] - data[index - period]
        result[index] = window / Decimal(period)
    return result


def ema(values, period=14, *, field="close"):
    """Return a Wilder-independent exponential moving average.

    The initial value is the SMA of the first ``period`` observations and the
    conventional smoothing factor is ``2 / (period + 1)``. Earlier positions
    are ``None``.
    """

    period = _period(period)
    return _ema_values(_values(values, field=field), period)


def rsi(values, period=14, *, field="close"):
    """Return Wilder relative-strength index values in the range 0..100.

    The first value is available after ``period`` price changes. A flat window
    is 50; a lossless window is 100 and a gainless window is 0.
    """

    period = _period(period)
    data = _values(values, field=field)
    result: list[DecimalValue] = [None] * len(data)
    if len(data) <= period:
        return result
    gains = []
    losses = []
    for previous, current in zip(data, data[1:], strict=False):
        change = current - previous
        gains.append(max(change, Decimal(0)))
        losses.append(max(-change, Decimal(0)))
    average_gain = sum(gains[:period], Decimal(0)) / Decimal(period)
    average_loss = sum(losses[:period], Decimal(0)) / Decimal(period)

    def value():
        if average_gain == 0 and average_loss == 0:
            return Decimal(50)
        if average_loss == 0:
            return Decimal(100)
        if average_gain == 0:
            return Decimal(0)
        return Decimal(100) - Decimal(100) / (Decimal(1) + average_gain / average_loss)

    result[period] = value()
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / Decimal(period)
        average_loss = (average_loss * (period - 1) + losses[index]) / Decimal(period)
        result[index + 1] = value()
    return result


def macd(values, fast_period=12, slow_period=26, signal_period=9, *, field="close"):
    """Return MACD, signal and histogram series using SMA-seeded EMAs.

    MACD becomes available when the slow EMA is available. The signal EMA is
    seeded from the first ``signal_period`` MACD values; all three lists retain
    the input length and use ``None`` during their respective warm-up periods.
    """

    fast_period = _period(fast_period, "fast_period")
    slow_period = _period(slow_period, "slow_period")
    signal_period = _period(signal_period, "signal_period")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    data = _values(values, field=field)
    fast = _ema_values(data, fast_period)
    slow = _ema_values(data, slow_period)
    line: list[DecimalValue] = [
        f - s if f is not None and s is not None else None
        for f, s in zip(fast, slow, strict=False)
    ]
    signal: list[DecimalValue] = [None] * len(data)
    available = [value for value in line if value is not None]
    if len(available) >= signal_period:
        alpha = Decimal(2) / Decimal(signal_period + 1)
        current = sum(available[:signal_period], Decimal(0)) / Decimal(signal_period)
        positions = [index for index, value in enumerate(line) if value is not None]
        signal[positions[signal_period - 1]] = current
        for position in positions[signal_period:]:
            line_value = line[position]
            assert line_value is not None
            current = (line_value - current) * alpha + current
            signal[position] = current
    histogram: list[DecimalValue] = [
        m - s if m is not None and s is not None else None
        for m, s in zip(line, signal, strict=False)
    ]
    return MACDResult(line, signal, histogram)


def bollinger_bands(values, period=20, multiplier=2, *, field="close"):
    """Return population-standard-deviation Bollinger Bands.

    The middle band is the SMA, and upper/lower bands are middle plus/minus
    ``multiplier * sqrt(sum((x - middle)^2) / period)``. Warm-up positions are
    ``None``; ``multiplier`` must be finite and non-negative.
    """

    period = _period(period)
    multiplier = _decimal(multiplier, "multiplier")
    if multiplier < 0:
        raise ValueError("multiplier must be non-negative")
    data = _values(values, field=field)
    middle = sma(data, period)
    upper: list[DecimalValue] = [None] * len(data)
    lower: list[DecimalValue] = [None] * len(data)
    for index in range(period - 1, len(data)):
        mean = middle[index]
        assert mean is not None
        variance = sum((value - mean) ** 2 for value in data[index - period + 1 : index + 1]) / Decimal(
            period
        )
        deviation = variance.sqrt()
        offset = multiplier * deviation
        upper[index] = mean + offset
        lower[index] = mean - offset
    return BollingerBandsResult(upper, middle, lower)


def atr(candles, period=14):
    """Return Wilder average true range from Candle OHLC data.

    True range uses ``high - low`` for the first candle and also considers the
    previous close thereafter. The first ATR is the arithmetic mean of the
    first ``period`` true ranges; earlier positions are ``None``.
    """

    period = _period(period)
    data = _candles(candles)
    result: list[DecimalValue] = [None] * len(data)
    if not data:
        return result
    true_ranges = [data[0].high - data[0].low]
    for previous, current in zip(data, data[1:], strict=False):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    if len(true_ranges) < period:
        return result
    current = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = (current * (period - 1) + true_ranges[index]) / Decimal(period)
        result[index] = current
    return result


def stochastic(candles, period=14, smooth_period=3):
    """Return raw stochastic %K and SMA-smoothed %D values.

    %K uses the close against the rolling low/high range and is scaled to
    0..100. A flat rolling range is defined as 50. %D is an SMA of available
    %K values, so the first ``period + smooth_period - 2`` positions are
    ``None``.
    """

    period = _period(period)
    smooth_period = _period(smooth_period, "smooth_period")
    data = _candles(candles)
    k: list[DecimalValue] = [None] * len(data)
    for index in range(period - 1, len(data)):
        window = data[index - period + 1 : index + 1]
        lowest = min(candle.low for candle in window)
        highest = max(candle.high for candle in window)
        spread = highest - lowest
        k[index] = Decimal(50) if spread == 0 else (data[index].close - lowest) / spread * 100
    d: list[DecimalValue] = [None] * len(data)
    positions = [index for index, value in enumerate(k) if value is not None]
    if len(positions) >= smooth_period:
        for offset in range(smooth_period - 1, len(positions)):
            position = positions[offset]
            d[position] = sum(
                (
                    cast(Decimal, k[positions[item]])
                    for item in range(offset - smooth_period + 1, offset + 1)
                ),
                Decimal(0),
            ) / Decimal(smooth_period)
    return StochasticResult(k, d)


simple_moving_average = sma
exponential_moving_average = ema
relative_strength_index = rsi
moving_average_convergence_divergence = macd
average_true_range = atr
stochastic_oscillator = stochastic


__all__ = [
    "BollingerBandsResult",
    "MACDResult",
    "StochasticResult",
    "atr",
    "average_true_range",
    "bollinger_bands",
    "ema",
    "exponential_moving_average",
    "macd",
    "moving_average_convergence_divergence",
    "relative_strength_index",
    "rsi",
    "simple_moving_average",
    "sma",
    "stochastic",
    "stochastic_oscillator",
]
