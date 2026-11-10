"""Polymarket Lab worker v3 (+ лог снимков живых окон для лаборатории).
Классические эджи (tag 21) + бинарный движок 5m/15m/1h "Up or Down":
справедливая вероятность из живой цены Binance, старта окна и волатильности.
Запуск каждые 5 минут. Без зависимостей.
Риск-фильтры v2 (по диагностированному механизму проигрышей - базис Binance/Chainlink
в концовке окна): без лонгшотов дешевле 0.10, без входа в последние 90 секунд.
"""
import json, math, os, re, time, urllib.request

GAMMA = "https://gamma-api.polymarket.com"
BIN = "https://data-api.binance.vision/api/v3/klines"
ST = {"state": "results/state.json", "signals": "results/signals.json", "scan": "results/scan.json", "sht": "results/sht.json", "lab": "results/sht_lab.json"}
CASH0 = 1000.0
MAX_POS = 6
HIST_TOP = 12
FEE = 0.002
BUDGET_S = 200
T0 = time.time()

SERIES = [
    ("btc-up-or-down-5m", "BTCUSDT", 300, "sht5"),
    ("btc-up-or-down-15m", "BTCUSDT", 900, "sht15"),
    ("btc-up-or-down-1h", "BTCUSDT", 3600, "sht1h"),
    ("eth-up-or-down-5m", "ETHUSDT", 300, "sht5"),
    ("eth-up-or-down-15m", "ETHUSDT", 900, "sht15"),
    ("sol-up-or-down-5m", "SOLUSDT", 300, "sht5"),
]
MAX_SHT_POS = 4
SHT_EDGE_MIN = 0.05
SHT_MIN_LIQ = 300.0

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "pm-lab/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def jload(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default

def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: json.dump(obj, f, ensure_ascii=False, indent=1)

def plist(s):
    try: return json.loads(s)
    except Exception: return []

def fnum(x):
    try: return float(x or 0)
    except Exception: return 0.0

# ---------- классический движок (tag 21) ----------
def scan():
    ms = []
    for off in range(0, 200, 100):
        if time.time() > T0 + BUDGET_S * 0.35: break
        url = (GAMMA + "/markets?tag_id=21&closed=false&active=true&limit=100&offset=%d" % off) + "&order=volume24hr&ascending=false"
        try: batch = fetch(url, 12)
        except Exception: break
        if not isinstance(batch, list) or not batch: break
        ms.extend(batch)
        if len(batch) < 100: break
    out = []
    for m in ms:
        try:
            pr = [fnum(x) for x in plist(m.get("outcomePrices", "[]"))]
            outs = plist(m.get("outcomes", "[]"))
            if len(pr) != 2 or len(outs) != 2: continue
            if not m.get("active") or m.get("closed"): continue
            end = (m.get("endDate") or "")[:10]
            try: end_epoch = time.mktime(time.strptime(end, "%Y-%m-%d"))
            except Exception: end_epoch = 0
            out.append({"id": str(m.get("id")), "slug": m.get("slug", ""),
                "question": (m.get("question") or m.get("groupItemTitle") or "")[:140],
                "yes": pr[0], "no": pr[1], "v24": fnum(m.get("volume24hr")),
                "v1w": fnum(m.get("volume1wk")), "liq": fnum(m.get("liquidityNum") or m.get("liquidity")),
                "end": end, "end_epoch": end_epoch, "accepting": bool(m.get("acceptingOrders"))})
        except Exception: continue
    return out

def hist(mid):
    url = GAMMA + "/markets/%s/prices-history?interval=1w&fidelity=60" % mid
    try: d = fetch(url, 8)
    except Exception: return []
    h = d.get("history") if isinstance(d, dict) else None
    pts = []
    if isinstance(h, list):
        for x in h:
            try: pts.append(float(x.get("p")))
            except Exception: pass
    return pts

def zscore(pts):
    tail = pts[-48:]
    if len(tail) < 12: return 0.0
    m = sum(tail) / len(tail)
    var = sum((p - m) ** 2 for p in tail) / len(tail)
    sd = math.sqrt(var) or 1e-9
    return (tail[-1] - m) / sd

def days_left(m):
    if not m["end_epoch"]: return 999
    return max(0.0, (m["end_epoch"] - time.time()) / 86400.0)

def make_signals(ms):
    sigs = []
    ms2 = sorted(ms, key=lambda x: -x["liq"])[:HIST_TOP]
    for m in ms2:
        if time.time() > T0 + BUDGET_S * 0.6: break
        pts = hist(m["id"])
        z = zscore(pts)
        p, q = m["yes"], m["no"]
        dl = days_left(m)
        def add(strategy, outcome, price, edge, note, tradeable=True):
            sigs.append({"strategy": strategy, "slug": m["slug"], "question": m["question"],
                         "outcome": outcome, "price": round(price, 4), "edge": round(edge, 4),
                         "note": note, "liq": round(m["liq"]), "v24": round(m["v24"]),
                         "z": round(z, 2), "end": m["end"], "days_left": round(dl, 1),
                         "tradeable": tradeable})
        s = p + q
        if s < 1 - 3 * FEE and m["liq"] >= 2000.0 and s > 0:
            side = "Yes" if p <= q else "No"
            add("arb", side, min(p, q), (1.0 - s) / s, "сумма исходов %.3f < 1 - издержки" % s)
        if len(pts) >= 40 and 0.10 <= p <= 0.92:
            base = pts[-min(72, len(pts) - 1)]
            chg = pts[-1] - base
            if chg > 0.05: add("momentum", "Yes", p, chg * 2, "рост +%.1f%% за 3 дня" % (chg * 100))
            elif chg < -0.05: add("momentum", "No", q, -chg * 2, "падение %.1f%% за 3 дня" % (chg * 100))
        if z < -2.0 and 0.15 <= p <= 0.85: add("mr", "Yes", p, -z * 0.02, "цена ниже нормы, z=%.1f" % z)
        if z > 2.0 and 0.15 <= q <= 0.85: add("mr", "No", q, z * 0.02, "цена выше нормы, z=%.1f" % z)
        if m["v1w"] > 0 and m["v24"] > 0 and m["v24"] > (m["v1w"] / 7.0) * 3.0:
            add("vols", "-", 0, 0, "объём 24ч %.0f против среднего %.0f" % (m["v24"], m["v1w"] / 7.0), False)
        if 0.78 <= p <= 0.95 and dl <= 30 and m["liq"] >= 10000: add("fav", "Yes", p, (1 - p) * 0.5, "фаворит %.0f%% до конца %.0f дн" % (p * 100, dl))
        if 0.78 <= q <= 0.95 and dl <= 30 and m["liq"] >= 10000: add("fav", "No", q, (1 - q) * 0.5, "фаворит (No) %.0f%% до конца %.0f дн" % (q * 100, dl))
    best = {}
    for s in sigs:
        if s["slug"] not in best or s["edge"] > best[s["slug"]]["edge"]:
            best[s["slug"]] = s
    return sorted(best.values(), key=lambda x: -x["edge"])[:20]

def close_pos(st, key, reason):
    pos = st["positions"].pop(key)
    px = pos.get("last", pos["entry"])
    ret = px / pos["entry"] - 1.0 - FEE
    st["cash"] += pos["shares"] * px * (1 - FEE / 2)
    st["closed"].append({"question": pos["question"], "slug": pos["slug"], "outcome": pos["outcome"],
                          "entry": pos["entry"], "exit": px, "ret_pct": round(ret * 100, 2),
                          "strategy": pos["strategy"], "opened": pos["opened"], "closed": now(),
                          "reason": reason})
    st["closed"] = st["closed"][-300:]

# ---------- бинарный движок 5m/15m/1h ----------
def phi(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))

def klines(sym):
    try: d = fetch(BIN + "?symbol=" + sym + "&interval=1m&limit=720", 12)
    except Exception: return []
    out = []
    if isinstance(d, list):
        for k in d:
            try: out.append((int(k[0]) // 1000, float(k[1]), float(k[4])))
            except Exception: pass
    return out

def sigma_m(kl):
    rets = []
    for i in range(1, len(kl)):
        if kl[i - 1][2] > 0 and kl[i][2] > 0:
            rets.append(math.log(kl[i][2] / kl[i - 1][2]))
    if len(rets) < 60: return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) or 1e-9

def sht_scan():
    kl_cache = {}
    windows = []
    t_now = time.time()
    for series_slug, sym, win_s, tag in SERIES:
        if time.time() > T0 + BUDGET_S * 0.75: break
        try:
            evs = fetch(GAMMA + "/events?series_slug=" + series_slug + "&closed=false&limit=60&order=endDate&ascending=true", 12)
        except Exception:
            continue
        if not isinstance(evs, list): continue
        for ev in evs:
            try:
                slug = ev.get("slug", "")
                mm = re.search(r"(\d{9,})$", slug)
                if not mm: continue
                start = int(mm.group(1))
                end = start + win_s
                if end < t_now: continue
                mkt = ev if ev.get("outcomePrices") else (ev.get("markets") or [{}])[0]
                pr = [fnum(x) for x in plist(mkt.get("outcomePrices", "[]"))]
                if len(pr) != 2 or pr[0] <= 0: continue
                liq = fnum(mkt.get("liquidityNum") or mkt.get("liquidity") or ev.get("liquidity"))
                windows.append({"series": series_slug, "tag": tag, "slug": slug,
                    "title": (ev.get("title") or "")[:80], "sym": sym, "win_s": win_s,
                    "start": start, "end": end, "p_up": pr[0], "p_down": pr[1], "liq": round(liq)})
            except Exception: continue
    for w in windows:
        if w["start"] > time.time() + 900: continue
        kl = kl_cache.setdefault(w["sym"], klines(w["sym"]))
        if not kl: continue
        sg = sigma_m(kl)
        if sg <= 0: continue
        start_px = None
        for ts, o, c in kl:
            if ts == w["start"]: start_px = o; break
        if start_px is None or start_px <= 0: continue
        cur = kl[-1][2]
        if cur <= 0: continue
        tau = (w["end"] - time.time()) / 60.0
        if tau < 0.5: continue
        d = math.log(cur / start_px)
        sig_tau = sg * math.sqrt(tau)
        fair = phi(d / sig_tau)
        if abs(d) < sig_tau * 0.05: fair += 0.01
        if not (0.02 <= fair <= 0.98): fair = min(0.98, max(0.02, fair))
        w["fair_up"] = round(fair, 4)
        w["edge_up"] = round(fair - w["p_up"], 4)
        w["edge_down"] = round((1.0 - fair) - w["p_down"], 4)
        w["tau_s"] = int(w["end"] - time.time())
    return windows

def sht_trade(st, windows):
    eq = st["cash"] + sum(p["shares"] * p.get("last", p["entry"]) for p in st["positions"].values())
    n_sht = sum(1 for p in st["positions"].values() if p.get("kind") == "sht")
    held = set(p["slug"] for p in st["positions"].values())
    for w in windows:
        if n_sht >= MAX_SHT_POS or st["cash"] < 15: break
        if "fair_up" not in w or w["slug"] in held: continue
        if w["liq"] < SHT_MIN_LIQ or w["tau_s"] < 90 or w["tau_s"] > w["win_s"] * 0.8: continue
        side, px, edge = None, 0.0, 0.0
        if w["edge_up"] >= SHT_EDGE_MIN: side, px, edge = "Up", w["p_up"], w["edge_up"]
        elif w["edge_down"] >= SHT_EDGE_MIN: side, px, edge = "Down", w["p_down"], w["edge_down"]
        if not side or px < 0.10 or px > 0.97: continue
        amt = min(0.05 * eq, st["cash"])
        if amt < 10: continue
        entry = px + 0.005
        shares = amt / entry
        st["cash"] -= amt
        st["positions"][w["slug"]] = {"kind": "sht", "slug": w["slug"],
            "question": w["title"], "series": w["series"],
            "outcome": side, "strategy": w["tag"], "entry": round(entry, 4),
            "last": round(entry, 4), "shares": round(shares, 2), "usd": round(amt, 2),
            "opened": now(), "epoch": time.time(), "end_ts": w["end"],
            "fair_up": w["fair_up"]}
        held.add(w["slug"])
        n_sht += 1

def sht_trade_streaks(st, windows):
    """Авто-стратегия моментума: включается ТОЛЬКО когда лаборатория подтвердила стрик-эдж (✅)."""
    strat = jload("results/strategies.json", {})
    sv = strat.get("streak_v1") or {}
    if not str(sv.get("status", "")).startswith("✅"): return
    probs = sv.get("probs") or {}
    pu, pd = probs.get("after_up"), probs.get("after_down")
    if pu is None or pd is None: return
    lab = jload(ST["lab"], {})
    eq = st["cash"] + sum(p["shares"] * p.get("last", p["entry"]) for p in st["positions"].values())
    n_sht = sum(1 for p in st["positions"].values() if p.get("kind") == "sht")
    held = set(p["slug"] for p in st["positions"].values())
    for w in windows:
        if n_sht >= MAX_SHT_POS or st["cash"] < 15: break
        if w["slug"] in held: continue
        prev_slug = re.sub(r"\d{9,}$", str(w["start"] - w["win_s"]), w["slug"])
        pv = (lab.get(prev_slug) or {}).get("outcome")
        if pv is None: continue
        tau = w["end"] - time.time()
        if tau < 90 or tau > w["win_s"] * 0.8 or w["liq"] < SHT_MIN_LIQ: continue
        side, px = None, 0.0
        if pv == 1 and pu - w["p_up"] >= SHT_EDGE_MIN: side, px = "Up", w["p_up"]
        elif pv == 0 and w["p_up"] - pd >= SHT_EDGE_MIN: side, px = "Down", w["p_down"]
        if not side or px < 0.10 or px > 0.97: continue
        amt = min(0.05 * eq, st["cash"])
        if amt < 10: continue
        entry = px + 0.005
        shares = amt / entry
        st["cash"] -= amt
        st["positions"][w["slug"]] = {"kind": "sht", "slug": w["slug"], "question": w["title"],
            "series": w["series"], "outcome": side, "strategy": "stk" + w["tag"][-2:],
            "entry": round(entry, 4), "last": round(entry, 4), "shares": round(shares, 2),
            "usd": round(amt, 2), "opened": now(), "epoch": time.time(), "end_ts": w["end"],
            "fair_up": round(pu if pv == 1 else pd, 4)}
        held.add(w["slug"])
        n_sht += 1

def settle_sht(st):
    for key in list(st["positions"].keys()):
        pos = st["positions"].get(key)
        if not pos or pos.get("kind") != "sht": continue
        if time.time() < pos["end_ts"] + 120: continue
        final = None
        if time.time() > pos["end_ts"] + 7200:
            final = 0.5
            reason = "нет данных расчёта"
        else:
            pr = None
            try:
                mk = fetch(GAMMA + "/markets?slug=" + pos["slug"], 10)
                if isinstance(mk, list) and mk:
                    pr = [fnum(x) for x in plist(mk[0].get("outcomePrices", "[]"))]
            except Exception:
                pr = None
            if not pr or len(pr) != 2 or max(pr) < 0.99:
                # закрытые окна удаляются из /markets - берём исход из /events
                try:
                    ev = fetch(GAMMA + "/events?slug=" + pos["slug"], 10)
                    if isinstance(ev, list) and ev:
                        e0 = ev[0]
                        src = e0 if e0.get("outcomePrices") else (e0.get("markets") or [{}])[0]
                        pr = [fnum(x) for x in plist(src.get("outcomePrices", "[]"))]
                except Exception:
                    pass
            if pr and len(pr) == 2 and max(pr) >= 0.99:
                idx = 0 if pos["outcome"] == "Up" else 1
                final = pr[idx]
                reason = "расчёт рынка"
        if final is None: continue
        st["positions"].pop(key)
        payout = pos["shares"] * final
        win = final >= 0.5
        st["cash"] += payout * (1 - FEE / 2)
        ret = payout / pos["usd"] - 1.0
        st["closed"].append({"question": pos["question"], "slug": pos["slug"],
            "outcome": pos["outcome"], "entry": pos["entry"], "exit": 1.0 if win else 0.0,
            "ret_pct": round(ret * 100, 2), "strategy": pos["strategy"],
            "opened": pos["opened"], "closed": now(),
            "reason": reason + (", выигрыш" if win else ", проигрыш")})
        st["closed"] = st["closed"][-300:]
        p_model = pos["fair_up"] if pos["outcome"] == "Up" else round(1.0 - pos["fair_up"], 4)
        st.setdefault("cal", []).append({"p": p_model, "win": 1 if win else 0, "tag": pos["strategy"]})
        st["cal"] = st["cal"][-500:]
        st["stats"] = st.get("stats") or {}
        st["stats"]["sht_trades"] = int(st["stats"].get("sht_trades", 0)) + 1
        st["stats"]["sht_pnl"] = round(float(st["stats"].get("sht_pnl", 0)) + payout - pos["usd"], 2)

LAB_CAP = 1500
LAB_SNAPS = 6

def lab_log(windows):
    """Снимки живых окон для лаборатории: время, цена Up, fair, ликвидность."""
    lab = jload(ST["lab"], {})
    if not isinstance(lab, dict): lab = {}
    t_now = int(time.time())
    for w in windows:
        key = w["slug"]
        e = lab.get(key)
        if e is None:
            e = {"series": w["series"], "tag": w["tag"], "sym": w["sym"],
                 "win_s": w["win_s"], "start": w["start"], "end": w["end"],
                 "snaps": [], "outcome": None}
            lab[key] = e
        snaps = e["snaps"]
        if not snaps or t_now - snaps[-1][0] >= 60:
            snaps.append([t_now, w["p_up"], w.get("fair_up"), w["liq"]])
            if len(snaps) > LAB_SNAPS: del snaps[0]
    for k in list(lab.keys()):
        if not isinstance(lab.get(k), dict) or lab[k].get("end", 0) < t_now - 30 * 86400:
            lab.pop(k, None)
    by = {}
    for k, e in lab.items(): by.setdefault(e["series"], []).append((e["start"], k))
    for ser, lst in by.items():
        if len(lst) > LAB_CAP:
            lst.sort()
            for start, k in lst[: len(lst) - LAB_CAP]: lab.pop(k, None)
    jsave(ST["lab"], lab)

def main():
    st = jload(ST["state"], {"cash": CASH0, "positions": {}, "closed": [], "curve": [[now(), CASH0]], "runs": 0, "done_keys": {}, "cal": []})
    settle_sht(st)
    ms = scan()
    by_slug = {}
    for m in ms: by_slug[m["slug"]] = m
    for key in list(st["positions"].keys()):
        pos = st["positions"].get(key)
        if pos is None or pos.get("kind") == "sht": continue
        m = by_slug.get(pos["slug"])
        if m is not None:
            pos["last"] = m["yes"] if pos["outcome"] == "Yes" else m["no"]
            if not m["accepting"]:
                close_pos(st, key, "рынок закрыт")
                continue
        else:
            pos.setdefault("last", pos["entry"])
        chg = pos["last"] / pos["entry"] - 1.0
        age = (time.time() - pos["epoch"]) / 86400.0
        if chg >= 0.10: close_pos(st, key, "тейк-профит +10%")
        elif chg <= -0.10: close_pos(st, key, "стоп-лосс -10%")
        elif age > 14.0: close_pos(st, key, "макс. срок 14 дней")
    sigs = make_signals(ms)
    eq = st["cash"] + sum(p["shares"] * p.get("last", p["entry"]) for p in st["positions"].values())
    held = set(p["slug"] for p in st["positions"].values())
    keys = st.setdefault("done_keys", {})
    if len(keys) > 300: keys.clear()
    for s in sigs:
        if len(st["positions"]) >= MAX_POS or st["cash"] < 20: break
        if not s["tradeable"] or s["slug"] in held or s["slug"] in keys: continue
        if not (0.06 <= s["price"] <= 0.94) or s["liq"] < 2000.0 or s["edge"] <= 0: continue
        amt = min(0.05 * eq, st["cash"])
        if amt < 10: continue
        shares = amt * (1 - FEE / 2) / s["price"]
        st["cash"] -= amt
        st["positions"][s["slug"]] = {"question": s["question"], "slug": s["slug"],
            "outcome": s["outcome"], "strategy": s["strategy"], "entry": s["price"],
            "last": s["price"], "shares": round(shares, 2), "usd": round(amt, 2),
            "opened": now(), "epoch": time.time()}
        held.add(s["slug"])
        keys[s["slug"]] = now()
    windows = sht_scan()
    sht_trade(st, windows)
    sht_trade_streaks(st, windows)
    lab_log(windows)
    eq = st["cash"] + sum(p["shares"] * p.get("last", p["entry"]) for p in st["positions"].values())
    st["curve"].append([now(), round(eq, 2)])
    st["curve"] = st["curve"][-1500:]
    st["runs"] = int(st.get("runs", 0)) + 1
    closed = st["closed"]
    wins = sum(1 for c in closed if c["ret_pct"] > 0)
    cal = st.get("cal", [])
    brier = round(sum((x["p"] - x["win"]) ** 2 for x in cal) / len(cal), 4) if cal else None
    acc = round(100.0 * sum(x["win"] for x in cal) / len(cal), 1) if cal else None
    st["stats"] = {"equity": round(eq, 2), "cash": round(st["cash"], 2), "open": len(st["positions"]),
                   "trades": len(closed), "wins": wins,
                   "winrate": round(100.0 * wins / len(closed), 1) if closed else 0.0,
                   "pnl_pct": round((eq / CASH0 - 1) * 100, 2), "updated": now(),
                   "runs": st["runs"],
                   "sht_open": sum(1 for p in st["positions"].values() if p.get("kind") == "sht"),
                   "sht_trades": int(st.get("stats", {}).get("sht_trades", 0)) if isinstance(st.get("stats"), dict) else 0,
                   "sht_pnl": (st.get("stats", {}) or {}).get("sht_pnl", 0) if isinstance(st.get("stats"), dict) else 0}
    jsave(ST["state"], st)
    jsave(ST["signals"], {"updated": now(), "scanned": len(ms), "signals": sigs})
    jsave(ST["scan"], {"updated": now(), "scanned": len(ms), "runs": st["runs"]})
    live = [w for w in windows if w["start"] <= time.time() < w["end"] or "fair_up" in w]
    live.sort(key=lambda x: x["end"])
    settled_sht = [c for c in closed if str(c.get("strategy", "")).startswith(("sht", "stk"))][::-1][:12]
    jsave(ST["sht"], {"updated": now(), "live": live[:14],
        "cal": {"n": len(cal), "brier": brier, "acc": acc},
        "settled": settled_sht})
    print("pm-lab v2: markets %d, windows %d, live %d, sht_open %d, equity %.2f" % (
        len(ms), len(windows), len(live), st["stats"]["sht_open"], eq))

if __name__ == "__main__":
    main()
