"""Unofficial Bynix SDK. No affiliation with the platform or Python Software Foundation."""

from .auth import AuthChain, BrowserAuth, PasswordAuth, SessionProvider, load_session, save_session
from .client import Bynix
from .data import candles_to_dataframe, candles_to_records, export_candles, resample_candles
from .journal import MemoryJournal, SQLiteJournal
from .models import AccountType, Candle, ConnectionEvent, Session, Tick
from .pool import ClientPool

__version__ = "1.0.0"
__all__ = [
    "AccountType",
    "AuthChain",
    "BrowserAuth",
    "Bynix",
    "Candle",
    "ClientPool",
    "ConnectionEvent",
    "MemoryJournal",
    "PasswordAuth",
    "SQLiteJournal",
    "Session",
    "SessionProvider",
    "Tick",
    "candles_to_dataframe",
    "candles_to_records",
    "export_candles",
    "load_session",
    "resample_candles",
    "save_session",
]
