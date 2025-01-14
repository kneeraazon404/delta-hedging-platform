# app/models/enums.py
from enum import Enum


class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"
