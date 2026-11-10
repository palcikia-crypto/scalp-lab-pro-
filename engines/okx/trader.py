"""OmniLab OKX trader v1.1 — реальные ордера OKX с фолбэком в paper.

PAPER (по умолчанию, без Secrets): живые свечи OKX 15m, виртуальные сделки
с теми же сигналами/рисками, что и в LIVE. Полностью автономно.

LIVE: задай Secrets OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE
(+ OKX_DEMO=1 → демо-счёт OKX).

Саморазвитие: параметры стратегии читаются из best_params.json, который
обновляет evolve-ядро (walk-forward оптимизация на живых данных OKX).
"""
import json
import os
import time

import pandas as pd

import config as C
from client import Okx

RES = "results/okx_live.json"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_best():
    """Параметры от evolve-ядра (walk-forward оптимизация)."""
    try:
        with open("best_params.json") as f:
            return json.load(f)
    except Exception:
        return {}


def load():
    try:
        with open(RES) as f:
            return json.load(f)
    except Exception:
        return {}


def save(st):
    os.makedirs("results", exist_ok=True)
    with open(RES, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def indicators(df, fast, slow):
    df = df.copy()
    df["ema_f"] = df["c"].ewm(span=fast, adjust=False).mean()
    df["ema_s"] = df["c"].ewm(span=slow, adjust=False).mean()
    prev = df["c"].shift()
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev).abs(), (df["l"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(C.ATR_PERIOD).mean()
    return df


def cross_signal(df):
    if len(df) < C.EMA_SLOW + C.ATR_PERIOD + 2:
        return 0, None, None
    if df["atr"].isna().iloc[-1]:
        return 0, None, None
    d = df["ema_f"] - df["ema_s"]
    if d.iloc[-2] <= 0 < d.iloc[-1]:
        return 1, float(df["c"].iloc[-1]), float(df["atr"].iloc[-1])
    if d.iloc[-2] >= 0 > d.iloc[-1]:
        return -1, float(df["c"].iloc[-1]), float(df["atr"].iloc[-1])
    return 0, None, None


def scan(api, best):
    out = {}
    fast = int(best.get("ema_fast", C.EMA_FAST))
    slow = int(best.get("ema_slow", C.EMA_SLOW))
    for inst in C.PAIRS:
        try:
            r = api.public_get("/api/v5/market/candles",
                               {"instId": inst, "bar": C.BAR, "limit": C.CANDLE_LIMIT})
            rows = r.get("data") or []
            if not rows:
                raise RuntimeError("no candles: " + str(r.get("error") or r)[:150])
            df = pd.DataFrame([x[:5] for x in rows], columns=["ts", "o", "h", "l", "c"])
            for col in ["o", "h", "l", "c"]:
                df[col] = df[col].astype(float)
            df = df.iloc[::-1].reset_index(drop=True)
            df = indicators(df, fast, slow)
            sig, price, atr = cross_signal(df)
            out[inst] = {"sig": sig, "price": price, "atr": atr, "err": None}
        except Exception as e:
            out[inst] = {"sig": 0, "price": None, "atr": None, "err": str(e)[:200]}
    return out, {"ema_fast": fast, "ema_slow": slow}


def add_trade(bucket, trade):
    bucket.insert(0, trade)
    del bucket[50:]


def paper_round(sigs, st, best):
    p = st.setdefault("paper", {"equity": C.PAPER_EQUITY, "start": C.PAPER_EQUITY,
                                "positions": {}, "trades": []})
    tp_atr = float(best.get("tp_atr", C.TP_ATR))
    sl_atr = float(best.get("sl_atr", C.SL_ATR))
    for inst, pos in list(p["positions"].items()):
        info = sigs.get(inst) or {}
        px = info.get("price")
        if not px:
            continue
        hit, reason = None, None
        if pos["side"] == 1:
            if px >= pos["tp"]:
                hit, reason = pos["tp"], "tp"
            elif px <= pos["sl"]:
                hit, reason = pos["sl"], "sl"
        else:
            if px <= pos["tp"]:
                hit, reason = pos["tp"], "tp"
            elif px >= pos["sl"]:
                hit, reason = pos["sl"], "sl"
        if hit is not None:
            pnl = pos["sz"] * (hit - pos["entry"]) * pos["side"]
            p["equity"] += pnl
            add_trade(p["trades"], {"ts": now(), "inst": inst,
                                    "side": "long" if pos["side"] == 1 else "short",
                                    "entry": round(pos["entry"], 6), "exit": round(hit, 6),
                                    "pnl": round(pnl, 4), "reason": reason})
            del p["positions"][inst]
    for inst, info in sigs.items():
        if len(p["positions"]) >= C.MAX_OPEN:
            break
        if inst in p["positions"] or not info.get("sig") or not info.get("price"):
            continue
        side, entry, atr = info["sig"], info["price"], info["atr"]
        p["positions"][inst] = {
            "side": side, "entry": entry, "sz": C.ORDER_USDT / entry,
            "tp": entry + side * tp_atr * atr,
            "sl": entry - side * sl_atr * atr,
            "opened": now(),
        }
    return p


def live_round(api, sigs, st, best):
    lv = st.setdefault("live", {"positions": {}, "trades": [], "start_eq": None})
    tp_atr = float(best.get("tp_atr", C.TP_ATR))
    sl_atr = float(best.get("sl_atr", C.SL_ATR))
    errors = []
    try:
        b = api.get("/api/v5/account/balance", {"ccy": "USDT"})
        det = (b.get("data") or [{}])[0].get("details") or []
        eq = next((float(d.get("eq") or 0) for d in det if d.get("ccy") == "USDT"), None)
        if eq is not None and lv["start_eq"] is None:
            lv["start_eq"] = eq
    except Exception as e:
        eq = None
        errors.append("balance: " + str(e)[:150])
    try:
        r = api.get("/api/v5/account/positions", {"instType": "SWAP"})
        api_pos = {}
        for pos in (r.get("data") or []):
            if float(pos.get("pos") or 0) != 0:
                api_pos[pos["instId"]] = float(pos.get("avgPx") or 0)
    except Exception as e:
        api_pos = None
        errors.append("positions: " + str(e)[:150])
    if api_pos is not None:
        for inst, meta in list(lv["positions"].items()):
            if inst not in api_pos:
                add_trade(lv["trades"], {"ts": now(), "inst": inst,
                                         "side": "long" if meta["side"] == 1 else "short",
                                         "entry": meta.get("entry"), "exit": None,
                                         "pnl": None, "reason": "tp/sl closed"})
                del lv["positions"][inst]
        for inst, meta in list(lv["positions"].items()):
            info = sigs.get(inst) or {}
            if info.get("sig") and info["sig"] == -meta["side"]:
                try:
                    r = api.post("/api/v5/trade/close-position",
                                 {"instId": inst, "mgnMode": C.MGN_MODE, "autoCxl": True})
                    if r.get("code") == "0":
                        add_trade(lv["trades"], {"ts": now(), "inst": inst,
                                                 "side": "long" if meta["side"] == 1 else "short",
                                                 "entry": meta.get("entry"), "exit": info.get("price"),
                                                 "pnl": None, "reason": "signal flip"})
                        del lv["positions"][inst]
                    else:
                        errors.append("close " + inst + ": " + str(r.get("msg") or r)[:150])
                except Exception as e:
                    errors.append("close " + inst + ": " + str(e)[:150])
    for inst, info in sigs.items():
        if len(lv["positions"]) >= C.MAX_OPEN:
            break
        if inst in lv["positions"] or not info.get("sig") or not info.get("price"):
            continue
        if api_pos is not None and inst in api_pos:
            continue
        side, price, atr = info["sig"], info["price"], info["atr"]
        tp = price + side * tp_atr * atr
        sl = price - side * sl_atr * atr
        try:
            meta = api.public_get("/api/v5/public/instruments",
                                  {"instType": "SWAP", "instId": inst})
            ctval = float((meta.get("data") or [{}])[0].get("ctVal") or 0)
            if not ctval:
                raise RuntimeError("no ctVal")
            lots = int(C.ORDER_USDT // (ctval * price))
            if lots < 1:
                errors.append("skip " + inst + ": ORDER_USDT ниже минимального контракта")
                continue
            api.post("/api/v5/account/set-leverage",
                     {"instId": inst, "lever": str(C.LEVERAGE), "mgnMode": C.MGN_MODE})
            r = api.post("/api/v5/trade/order", {
                "instId": inst, "tdMode": C.MGN_MODE,
                "side": "buy" if side == 1 else "sell",
                "ordType": "market", "sz": str(lots),
                "attachAlgoOrds": [{
                    "tpTriggerPx": "%.6g" % tp, "tpOrdPx": "-1",
                    "slTriggerPx": "%.6g" % sl, "slOrdPx": "-1",
                }],
            })
            if r.get("code") == "0":
                lv["positions"][inst] = {"side": side, "entry": price, "sz_lots": lots,
                                         "tp": round(tp, 6), "sl": round(sl, 6), "opened": now()}
            else:
                errors.append("open " + inst + ": " + str(r.get("msg") or r)[:150])
        except Exception as e:
            errors.append("open " + inst + ": " + str(e)[:150])
    return lv, eq, errors


def main():
    st = load()
    best = load_best()
    key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_API_SECRET", "")
    passph = os.environ.get("OKX_API_PASSPHRASE", "")
    demo = os.environ.get("OKX_DEMO", "").strip().lower() in ("1", "true", "yes", "demo")
    mode = "live" if (key and secret and passph) else "paper"
    api = Okx(key, secret, passph, demo=demo)

    sigs, params = scan(api, best)
    p = paper_round(sigs, st, best)
    eq, errors = None, []
    if mode == "live":
        lv, eq, errors = live_round(api, sigs, st, best)
    else:
        lv = st.get("live", {"positions": {}, "trades": []})

    if mode == "paper":
        equity = p["equity"]
        start = p.get("start", C.PAPER_EQUITY)
    else:
        equity = eq if eq is not None else st.get("last_live_eq")
        start = st.get("live", {}).get("start_eq") or equity or C.PAPER_EQUITY
        if eq is not None:
            st["last_live_eq"] = eq
    pnl_pct = round((equity / start - 1) * 100, 3) if (equity and start) else None
    trades = len(p["trades"]) + len(lv.get("trades", []))
    open_n = len(p["positions"]) + len(lv.get("positions", []))

    st.update({
        "mode": mode, "demo": demo if mode == "live" else None,
        "equity": round(equity, 2) if equity is not None else None,
        "start_equity": round(start, 2) if start else None,
        "pnl_pct": pnl_pct, "trades": trades, "open": open_n,
        "params": params, "params_source": "evolve" if best else "default",
        "signals": {k: {"sig": v["sig"], "price": v["price"], "err": v["err"]}
                    for k, v in sigs.items()},
        "errors": errors[:10], "updated": now(),
    })
    save(st)
    print("okx trader: mode=%s equity=%s open=%s trades=%s errors=%d" % (
        mode, st["equity"], open_n, trades, len(errors)))


if __name__ == "__main__":
    main()
