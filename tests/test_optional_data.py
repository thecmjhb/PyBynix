from decimal import Decimal

import pytest

from pybynix import Candle, candles_to_dataframe


def test_dataframe_retains_decimal_and_utc():
    pytest.importorskip("pandas")
    candles = [
        Candle(
            60,
            Decimal("1.1234567890123456789"),
            Decimal(2),
            Decimal(1),
            Decimal("1.9"),
            Decimal("3.2"),
        )
    ]
    frame = candles_to_dataframe(candles)
    assert str(frame.index.tz) == "UTC"
    assert frame.iloc[0]["open"] == Decimal("1.1234567890123456789")
    assert isinstance(frame.iloc[0]["open"], Decimal)


def test_empty_dataframe_keeps_the_utc_index_schema():
    pytest.importorskip("pandas")
    frame = candles_to_dataframe([])
    assert frame.empty
    assert str(frame.index.tz) == "UTC"
