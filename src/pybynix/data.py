"""Portable market-data export and explicit candle resampling."""

import csv
import json
from decimal import Decimal
from pathlib import Path

from .models import Candle


def candles_to_records(candles):
    return [
        {
            "timestamp": c.timestamp,
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": str(c.volume),
        }
        for c in candles
    ]


def export_candles(candles, path, *, format=None):
    path = Path(path)
    kind = (format or path.suffix.lstrip(".")).lower()
    if kind not in {"csv", "json"}:
        raise ValueError("Choose CSV or JSON")
    records = candles_to_records(candles)
    if kind == "json":
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
            )
            writer.writeheader()
            writer.writerows(records)


def candles_to_dataframe(candles):
    import pandas as pd

    frame = pd.DataFrame(
        candles_to_records(candles), columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    # Keep Decimal values rather than silently lowering monetary precision.
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = frame[column].map(Decimal)
    return frame.set_index("datetime")


def resample_candles(candles, *, source_seconds, target_seconds, complete_only=True):
    if (
        isinstance(source_seconds, bool)
        or isinstance(target_seconds, bool)
        or not isinstance(source_seconds, int)
        or not isinstance(target_seconds, int)
        or source_seconds < 1
        or target_seconds < source_seconds
        or target_seconds % source_seconds
    ):
        raise ValueError("Target must be an integer multiple of the positive source interval")
    groups = {}
    for candle in sorted({c.timestamp: c for c in candles}.values(), key=lambda c: c.timestamp):
        bucket = candle.timestamp // target_seconds * target_seconds
        groups.setdefault(bucket, []).append(candle)
    result = []
    for timestamp, values in sorted(groups.items()):
        expected = list(range(timestamp, timestamp + target_seconds, source_seconds))
        if complete_only and [c.timestamp for c in values] != expected:
            continue
        result.append(
            Candle(
                timestamp,
                values[0].open,
                max(c.high for c in values),
                min(c.low for c in values),
                values[-1].close,
                sum((c.volume for c in values), Decimal(0)),
            )
        )
    return result
