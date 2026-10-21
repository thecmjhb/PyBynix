"""The observed web display's candle-pressure + tick-direction EMA model."""

from decimal import ROUND_FLOOR, Decimal


class SentimentTracker:
    def __init__(self):
        self.pressure = Decimal("0.5")
        self.tick_ema = Decimal("0.5")
        self.last_price = None
        self.samples = 0

    def set_candles(self, candles):
        positive = negative = Decimal(0)
        self.samples = len(candles)
        for index, candle in enumerate(candles):
            change = (candle.close - candle.open) / (candle.open or 1)
            weight = Decimal("0.5") + Decimal(index) / len(candles)
            positive += max(change, Decimal(0)) * weight
            negative += max(-change, Decimal(0)) * weight
        total = positive + negative
        self.pressure = positive / total if total else Decimal("0.5")

    def update(self, price):
        price = Decimal(str(price))
        if not price.is_finite() or price <= 0:
            raise ValueError("Finite positive price required")
        if self.last_price is not None and self.last_price != price:
            direction = Decimal(int(price > self.last_price))
            self.tick_ema = Decimal("0.94") * self.tick_ema + Decimal("0.06") * direction
        self.last_price = price

    def snapshot(self):
        raw = 100 * (Decimal("0.65") * self.pressure + Decimal("0.35") * self.tick_ema)
        up = min(95, max(5, int((raw + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))))
        return {
            "up": up,
            "down": 100 - up,
            "source": "platform_style_derived_sentiment",
            "candle_pressure": self.pressure,
            "tick_ema": self.tick_ema,
            "samples": self.samples,
            "is_trader_positioning": False,
        }
