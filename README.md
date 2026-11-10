# ⚡ OmniLab — единая автономная квант-лаборатория

**OmniLab** объединяет два проверенных исследовательских проекта в одну самостоятельную систему, работающую полностью на GitHub Actions без сервера:

- 🎯 **Polymarket Live** (из polymarket-lab): paper-трейдинг бинарных окон 5m/15m/1h «Up or Down» (BTC/ETH/SOL) — справедливая вероятность из живой цены Binance, риск-фильтры v2, автоматический расчёт сделок.
- 🔬 **Research Lab** (из polymarket-lab): проверка 8 гипотез с хронологическим сплитом 70/30 (IS/OOS), протокол против подгонки — ничего не перебирается.
- 📊 **Scalp Backtests** (из my-termux-project): walk-forward бэктесты 30+ стратегий на 20 парах Binance Futures (1m/5m/15m/1h), комиссии и проскальзывание учтены, паттерны проходят поправку Бенджамини-Хохберга (FDR 5%).
- 💵 **Paper Trading** (из my-termux-project): топ-стратегии бэктестов торгуют виртуально на живых данных.
- 🖥️ **Единый дашборд** на GitHub Pages: бог-обзор всех модулей, здоровье системы, лидерборды, исследования.

## Архитектура

```
GitHub Actions (бессерверно, 24/7)
├── pm-worker   каждые 5 мин   → engines/pm_worker.py  → results/           (Polymarket live)
├── pm-lab      каждые 30 мин  → engines/pm_lab.py     → results/lab.json    (исследования)
├── bt-worker   каждые 30 мин  → engines/scalp/worker.py → results/, reports/ (бэктесты + paper)
├── reporter    после каждого  → reporter.py           → results/universe.json (бог-обзор)
└── pages       каждые 30 мин  → docs/index.html       → GitHub Pages (дашборд)
```

## Единый дашборд

После первого деплоя: **https://palcikia-crypto.github.io/scalp-lab-pro/**

Вкладки: Обзор (здоровье системы + ключевые метрики) · Polymarket (equity, живые окна, калибровка) · Бэктесты (лидерборд OOS, paper-трейдинг) · Исследования (гипотезы, golden-закономерности).

## Протоколы против подгонки (унаследованы и сохранены)

1. Пороги и гипотезы зафиксированы заранее — ничего не перебирается на новых данных.
2. Хронологический сплит 70/30: закономерность 
подтверждена только при согласии IS и OOS.
3. Walk-forward: параметры подбираются на одном окне, результат считается на следующем.
4. Множественные тесты: поправка Бенджамини-Хохберга (FDR 5%).
5. Комиссии (taker 0.05% / maker 0.02%) и проскальзывание учтены везде.

## Файлы

| Путь | Что делает |
|---|---|
| `engines/pm_worker.py` | Polymarket live: скан рынков, окна 5m/15m/1h, paper-сделки, расчёты |
| `engines/pm_lab.py` | Исследовательская лаборатория: 8 гипотез, IS/OOS, калибровка |
| `engines/scalp/` | Walk-forward бэктесты, 30+ стратегий, эволюция очереди, paper-трейдинг |
| `reporter.py` | Единый бог-обзор → `results/universe.json` |
| `docs/index.html` | Единый дашборд (GitHub Pages) |
| `results/` | Состояние Polymarket-движка и лаборатории |
| `engines/scalp/results/` | Результаты бэктестов, очередь, прогресс |
| `engines/scalp/reports/` | REPORT.md, leaderboard.csv |

## Источники

Движки перенесены без изменений из рабочих проектов:
- [polymarket-lab](https://github.com/palcikia-crypto/polymarket-lab) — `autolab/worker.py`, `autolab/lab.py`
- [my-termux-project](https://github.com/palcikia-crypto/my-termux-project) — `scalp-lab/`

Оригинальные проекты продолжают работать независимо и не изменялись.

## Лицензия

MIT. Всё программное обеспечение — для исследовательских целей; paper-трейдинг использует виртуальные средства, реальных денег нет.
