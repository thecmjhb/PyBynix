"""Unofficial Bynix SDK. No affiliation with the platform or Python Software Foundation."""

from .auth import AuthChain, BrowserAuth, PasswordAuth, SessionProvider, load_session, save_session
from .client import Bynix
from .data import candles_to_dataframe, candles_to_records, export_candles, resample_candles
from .errors import AccessDenied, ChallengeRequired
from .indicators import (
    BollingerBandsResult,
    MACDResult,
    StochasticResult,
    atr,
    average_true_range,
    bollinger_bands,
    ema,
    exponential_moving_average,
    macd,
    moving_average_convergence_divergence,
    relative_strength_index,
    rsi,
    simple_moving_average,
    sma,
    stochastic,
    stochastic_oscillator,
)
from .journal import MemoryJournal, SQLiteJournal
from .market import MarketLimits, MarketSession, MarketSessionManager
from .models import AccountType, Candle, ConnectionEvent, Session, Tick
from .orchestration import (
    AutoExecutor,
    DecisionCallback,
    ExecutionIntent,
    ExecutionOrchestrator,
    ExecutionRecord,
    OrderIntent,
)
from .pool import ClientPool
from .runtime import RequestPacer
from .session import BynixSessionManager, FileSessionStore

__version__ = "1.0.2"
__all__ = [
    "AccessDenied",
    "AccountType",
    "AuthChain",
    "BollingerBandsResult",
    "BrowserAuth",
    "Bynix",
    "BynixSessionManager",
    "Candle",
    "ChallengeRequired",
    "ClientPool",
    "ConnectionEvent",
    "FileSessionStore",
    "RequestPacer",
    "AutoExecutor",
    "DecisionCallback",
    "ExecutionIntent",
    "ExecutionOrchestrator",
    "ExecutionRecord",
    "MemoryJournal",
    "MACDResult",
    "MarketLimits",
    "MarketSession",
    "MarketSessionManager",
    "PasswordAuth",
    "SQLiteJournal",
    "Session",
    "SessionProvider",
    "StochasticResult",
    "Tick",
    "OrderIntent",
    "candles_to_dataframe",
    "candles_to_records",
    "export_candles",
    "load_session",
    "atr",
    "average_true_range",
    "bollinger_bands",
    "ema",
    "exponential_moving_average",
    "macd",
    "moving_average_convergence_divergence",
    "relative_strength_index",
    "resample_candles",
    "rsi",
    "save_session",
    "simple_moving_average",
    "sma",
    "stochastic",
    "stochastic_oscillator",
]
