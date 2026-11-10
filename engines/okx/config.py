# OmniLab OKX live-трейдинг — настройки и риски (v1.0.1)
import os

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
ORDER_USDT = float(os.environ.get("OKX_ORDER_USDT", "5"))   # $ на позицию
LEVERAGE = int(os.environ.get("OKX_LEVERAGE", "2"))         # плечо
MAX_OPEN = int(os.environ.get("OKX_MAX_OPEN", "2"))         # максимум одновременных позиций
MGN_MODE = "cross"
PAPER_EQUITY = 1000.0
