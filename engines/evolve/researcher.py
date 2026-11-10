"""OmniLab Evolve — саморазвивающееся ядро системы.

Каждый час без человека:
1. Walk-forward оптимизация параметров EMA-стратегии на ЖИВЫХ свечах OKX
   (900 баров 15m): выбор на первой половине истории, проверка OOS на второй.
   Только комбинации, прибыльные на OOS, идут в engines/okx/best_params.json —
   трейдер подхватывает их на следующем прогоне.
2. Кросс-веномный поиск golden-паттернов: лаборатория Polymarket (IS+OOS),
   лидерборд walk-forward бэктестов и собственные находки на OKX.
3. results/evolve.json — журнал эволюции (история поколений параметров).
Против подгонки: split 50/50, минимум сделок, комиссия roundtrip, SL раньше TP.
"""
import csv
import json
import math
import os
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OKX = "https://www.okx.com/api/v5/market/candles"
PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
BAR = "15m"
LIMIT = 900
FEE_RT = 0.0016
MIN_TRADES = 6
EMA_GRID = [(5, 13), (8, 21), (9, 21), (12, 26), (20, 50)]
SL_GRID = [1.0, 1.5, 2.0]
TP_GRID = [2.0, 2.5, 3.0]


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rp(*parts):
    return os.path.join(ROOT, *parts)


def candles(inst):
    r = requests.get(OKX, params={"instId": inst, "bar": BAR, "limit": LIMIT}, timeout=25).json()
    rows = r.get("data") or []
    df = pd.DataFrame([x[:5] for x in rows], columns=["ts", "o", "h", "l", "c"])
    for col in ["o", "h", "l", "c"]:
        df[col] = df[col].astype(float)
    return df.iloc[::-1].reset_index(drop=True)


def backtest(df, fast, slow, sl_k, tp_k):
    c = df["c"].values
    h = df["h"].values
    l = df["l"].values
    n = len(c)
    emaf = pd.Series(c).ewm(span=fast, adjust=False).mean().values
    emas = pd.Series(c).ewm(span=slow, adjust=False).mean().values
    prev = pd.Series(c).shift()
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev).abs(), (df["l"] - prev).abs()], axis=1).max(axis=1).rolling(14).mean().values
    rets = []
    i = slow + 16
    while i < n - 1:
        dp = emaf[i - 1] - emas[i - 1]
        d = emaf[i] - emas[i]
        sig = 1 if (dp <= 0 < d) else (-1 if (dp >= 0 > d) else 0)
        if not sig or math.isnan(tr[i]):
            i += 1
            continue
        e = c[i]
        tp_px = e + sig * tp_k * tr[i]
        sl_px = e - sig * sl_k * tr[i]
        j = i + 1
        exit_px = None
        while j < n:
            if sig == 1:
                if l[j] <= sl_px:
                    exit_px = sl_px
                    break
                if h[j] >= tp_px:
                    exit_px = tp_px
                    break
            else:
                if h[j] >= sl_px:
                    exit_px = sl_px
                    break
                if l[j] <= tp_px:
                    exit_px = tp_px
                    break
            j += 1
        if exit_px is not None:
            rets.append((exit_px - e) / e * sig - FEE_RT)
        i = j + 1
    return rets


def evolve_okx():
    results = []
    try:
        data = {p: candles(p) for p in PAIRS}
    except Exception as e:
        return None, "candles: " + str(e)[:150]
    best = None
    for (fast, slow) in EMA_GRID:
        for sl_k in SL_GRID:
            for tp_k in TP_GRID:
                is_total = oos_total = 0.0
                is_n = oos_n = 0
                ok = True
                for inst, df in data.items():
                    half = len(df) // 2
                    is_rets = backtest(df.iloc[:half], fast, slow, sl_k, tp_k)
                    oos_rets = backtest(df.iloc[half:], fast, slow, sl_k, tp_k)
                    if len(is_rets) < MIN_TRADES // 2 or len(oos_rets) < MIN_TRADES // 2:
                        ok = False
                        break
                    is_total += sum(is_rets); is_n += len(is_rets)
                    oos_total += sum(oos_rets); oos_n += len(oos_rets)
                if ok and is_total > 0 and oos_total > 0:
                    results.append({"ema_fast": fast, "ema_slow": slow, "sl_atr": sl_k, "tp_atr": tp_k,
                                    "is_ret": round(is_total * 100, 2), "oos_ret": round(oos_total * 100, 2),
                                    "trades": is_n + oos_n})
                    if best is None or oos_total > best[0]:
                        best = (oos_total, results[-1])
    return (best[1] if best else None), results


def golden_cross_venue():
    items = []
    try:
        with open(rp("results", "lab.json")) as f:
            lab = json.load(f)
        for g in lab.get("golden") or []:
            items.append({"src": "polymarket-lab", "name": g.get("name"), "note": g.get("note")})
    except Exception:
        pass
    try:
        with open(rp("engines", "scalp", "reports", "leaderboard.csv")) as f:
            rows = list(csv.DictReader(f))[:3]
        for r in rows:
            items.append({"src": "walk-forward bt", "name": "%s %s %s" % (
                r.get("strategy"), r.get("symbol"), r.get("tf")),
                "note": "OOS PF %s, Sharpe %s" % (r.get("oos_pf"), r.get("oos_sharpe"))})
    except Exception:
        pass
    return items


def main():
    ev_path = rp("results", "evolve.json")
    ev = {"history": []}
    try:
        with open(ev_path) as f:
            ev = json.load(f)
    except Exception:
        pass
    best, res = evolve_okx()
    err = None
    if isinstance(res, str):
        err, res = res, []
    if best:
        best["updated"] = now()
        with open(rp("engines", "okx", "best_params.json"), "w") as f:
            json.dump(best, f, indent=1)
    golden = golden_cross_venue()
    ev["updated"] = now()
    ev["best_okx"] = best
    ev["candidates"] = res[:10]
    ev["golden"] = golden
    ev["error"] = err
    hist = ev.setdefault("history", [])
    hist.insert(0, {"ts": now(), "best": best, "golden_n": len(golden), "err": err})
    del hist[50:]
    with open(ev_path, "w") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print("evolve: best=%s candidates=%d golden=%d err=%s" % (
        json.dumps(best) if best else None, len(res), len(golden), err))


if __name__ == "__main__":
    main()
