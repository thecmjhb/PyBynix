from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class AccountType(StrEnum):
    DEMO = "demo"
    REAL = "real"


@dataclass(frozen=True)
class Session:
    token: str = field(repr=False)
    host: str = "bynixbrokers.com"

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token.strip():
            raise ValueError("A nonempty Bynix access token is required")


@dataclass(frozen=True)
class Candle:
    timestamp: int  # Unix seconds, UTC; unlike tick timestamps which are milliseconds
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_wire(cls, value: dict[str, Any]):
        return cls(
            int(value["timestamp"]),
            *(Decimal(str(value[k])) for k in ("open", "high", "low", "close")),
            Decimal(str(value.get("volume", 0))),
        )


@dataclass(frozen=True)
class Tick:
    symbol: str
    interval: str
    price: Decimal
    timestamp: int  # Unix milliseconds
    server_time: int
    candle: Candle | None
    closed: Candle | None
    payout: Decimal | None

    @classmethod
    def from_wire(cls, value):
        return cls(
            value["symbol"],
            value["interval"],
            Decimal(str(value["price"])),
            int(value["timestamp"]),
            int(value["server_time"]),
            Candle.from_wire(value["candle"]) if value.get("candle") else None,
            Candle.from_wire(value["closed"]) if value.get("closed") else None,
            Decimal(str(value["payout"])) if value.get("payout") is not None else None,
        )


@dataclass(frozen=True)
class ConnectionEvent:
    kind: str
    generation: int
    detail: str = ""
