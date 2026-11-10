"""OmniLab Polymarket CLOB trader — реальные ордера на Polymarket.

Активен только при наличии Secrets POLY_PRIVATE_KEY (private key кошелька)
и POLY_ADDRESS (funder/proxy address). Без них тихо завершается —
paper-трейдинг остаётся в pm-worker, ничего не ломается.

Логика: берёт лучший сигнал из results/signals.json (edge >= POLY_MIN_EDGE,
default 0.05), находит clobTokenIds через gamma-API по slug и ставит
маркет-ордер на POLY_ORDER_USDC (default $1). Риск ограничен одной сделкой
за прогон. Все события — в results/clob.json.
"""
import json
import os
import time

import requests

RES = "results/clob.json"
GAMMA = "https://gamma-api.polymarket.com/markets"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    res = {"updated": now()}
    key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    funder = os.environ.get("POLY_ADDRESS", "").strip()
    usdc = float(os.environ.get("POLY_ORDER_USDC", "1"))
    min_edge = float(os.environ.get("POLY_MIN_EDGE", "0.05"))

    if not key or not funder:
        res.update({"mode": "off", "note": "нет POLY_PRIVATE_KEY/POLY_ADDRESS — реальная торговля выключена"})
        dump(res)
        print(res["note"])
        return

    try:
        with open("results/signals.json") as f:
            sig = json.load(f)
    except Exception as e:
        res.update({"mode": "off", "error": "signals.json: " + str(e)[:150]})
        dump(res)
        return

    best, best_edge = None, 0.0
    for s in sig.get("signals", []):
        try:
            if not s.get("tradeable", False):
                continue
            edge = float(s.get("edge") or 0)
            if edge >= min_edge and edge > best_edge:
                best, best_edge = s, edge
        except Exception:
            continue
    if not best:
        res.update({"mode": "live", "note": "нет сигнала с edge >= %s" % min_edge})
        dump(res)
        print(res["note"])
        return

    res.update({"mode": "live", "picked": {"slug": best.get("slug"), "question": best.get("question"),
                                           "outcome": best.get("outcome"), "edge": best_edge,
                                           "price": best.get("price")}})
    try:
        m = requests.get(GAMMA, params={"slug": best["slug"]}, timeout=15).json()
        if not m:
            raise RuntimeError("gamma: рынок не найден")
        tokens = json.loads(m[0].get("clobTokenIds") or "[]")
        if len(tokens) < 2:
            raise RuntimeError("gamma: нет clobTokenIds")
        side_up = str(best.get("outcome", "")).lower() in ("yes", "up", "выше")
        token = tokens[0] if side_up else tokens[1]

        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import MarketOrderArgs, OrderType

        client = ClobClient("https://clob.polymarket.com", key=key,
                             chain_id=137, funder=funder)
        signed = client.create_market_order(MarketOrderArgs(token_id=token, amount=usdc))
        resp = client.post_order(signed, OrderType.GFD)
        res["order"] = json.loads(json.dumps(resp, default=str))[:500] if not isinstance(resp, dict) else {
            "status": resp.get("status", resp.get("success")), "orderID": resp.get("orderID")}
        res["note"] = "ордер отправлен: $%s на '%s' (edge %s)" % (usdc, best.get("question"), best_edge)
    except Exception as e:
        res["error"] = str(e)[:300]
    dump(res)
    print(json.dumps(res, ensure_ascii=False)[:400])


def dump(res):
    os.makedirs("results", exist_ok=True)
    with open(RES, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
