# 🔑 Включение реальной торговли (Live)

Оба движка уже работают 24/7: **без ключей — paper-режим на живых данных**,
с ключами — реальные ордера. Ничего в коде менять не нужно.

## OKX (фьючерсы BTC/ETH/SOL)

1. OKX → Profile → API → Create API Key, права: **Read + Trade** (вывод НЕ нужен).
   В настройках ключа разреши IP: добавь диапазоны GitHub Actions
   (см. https://api.github.com/meta → поле `actions`) или постав галочку
   «разрешить любой IP» (менее безопасно).
2. В scalp-lab-pro: **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Значение |
   |---|---|
   | `OKX_API_KEY` | apiKey |
   | `OKX_API_SECRET` | secretKey |
   | `OKX_API_PASSPHRASE` | passphrase |
   | `OKX_DEMO` | `1` для демо-счёта OKX (советую начать с него), пусто/удалить → боевой счёт |

3. (Опционально) Variables (не секреты): `OKX_ORDER_USDT` (default 5),
   `OKX_LEVERAGE` (default 2), `OKX_MAX_OPEN` (default 2).

Порядок открывается маркетом по пересечению EMA(9/21) на 15m, TP=2.5×ATR,
SL=1.5×ATR прикрепляются к ордеру на стороне OKX (работают даже если воркер упал).

## Polymarket (реальные ставки на рынках)

1. Экспортируй private key кошелька Polymarket (MetaMask / Polymarket wallet).
   На счету должен быть USDC (Polygon) и открыт апрув для CTF Exchange.
2. Secrets:

   | Secret | Значение |
   |---|---|
   | `POLY_PRIVATE_KEY` | приватный ключ кошелька |
   | `POLY_ADDRESS` | адрес прокси-кошелька Polymarket (funder) |
   | `POLY_ORDER_USDC` | размер ставки, default 1 |
   | `POLY_MIN_EDGE` | минимальный edge сигнала, default 0.05 |

⚠️ Приватный ключ = полный доступ к кошельку. Используй отдельный кошелёк
с небольшой суммой. Начни с демо OKX и малых размеров.
