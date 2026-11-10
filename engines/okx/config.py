# OmniLab OKX live-трейдинг — настройки и риски (v1.0.1)
import os


def _env(name, cast, default):
    v = (os.environ.get(name) or "").strip()
    if not v:
        return default
    try:
        return cast(v)
    except ValueError:
        return default


# Фьючерсы USDT (SWAP). Для спота замени на "BTC-USDT" и tdMode="cash" в trader.py
PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
BAR = "15m"
CANDLE_LIMIT = 300

# Сигнал: пересечение EMA(9/21), волатильность ATR(14)
EMA_FAST = 9
EMA_SLOW = 21
ATR_PERIOD = 14
SL_ATR = 1.5     # стоп = 1.5 * ATR
TP_ATR = 2.5     # тейк = 2.5 * ATR (R:R ~ 1.67)

# Риск-менеджмент (консервативный старт — увеличь после обкатки)
# Пустые/битые значения в Secrets и Variables не роняют движок — берётся дефолт.
ORDER_USDT = _env("OKX_ORDER_USDT", float, 5.0)   # $ на позицию
LEVERAGE = _env("OKX_LEVERAGE", int, 2)          # плечо
MAX_OPEN = _env("OKX_MAX_OPEN", int, 2)          # максимум одновременных позиций
MGN_MODE = "cross"
PAPER_EQUITY = 1000.0
