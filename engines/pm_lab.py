"""Polymarket Lab - исследовательская лаборатория бинарных окон 5m/15m/1h.

Протокол против подгонки (главный принцип лаборатории):
  1) гипотезы и пороги зафиксированы ЗАРАНЕЕ (константы ниже), ничего не перебирается;
  2) хронологический сплит 70/30: гипотеза подтверждена только если эффект
     одного знака и на истории (IS), и на "будущем" хвосте (OOS);
  3) минимумы данных: бин калибровки n>=50, стратегия OOS n>=30, иначе "копим";
  4) гипотез проверяется ~10, значит ~1 ложное срабатывание ожидаемо случайно:
     без согласия IS и OOS ничто не объявляется закономерностью.
Данные: results/sht_lab.json (пишет worker v3: снимки живых окон каждые 5 мин).
Вывод: results/lab.json для дашборда. Запуск каждые 30 минут. Без зависимостей.
"""
import json, math, os, re, time, urllib.request

GAMMA = "https://gamma-api.polymarket.com"
SRC = "results/sht_lab.json"
OUT = "results/lab.json"
T0 = time.time()
BUDGET_S = 200

SETTLE_BATCH = 30    # сколько окон размечаем за прогон
SPLIT = 0.7          # доля IS (хронологически первых)
MIN_OOS = 30         # минимум объектов на OOS для вердикта
MIN_BIN = 50         # минимум окон в бине калибровки
BIAS_EPS = 0.05      # сдвиг >= 5 вероятностных пунктов = кандидат на эдж
STK_DEV = 0.10       # отклонение стрика от 50% для флага
EDGE_MIN = 0.05      # правило из worker (копия, не подбирается)
MIN_LIQ = 300.0
PX_MIN, PX_MAX = 0.10, 0.97   # риск-фильтр v2: без лонгшотов < 0.10 (базис Binance/Chainlink)
TAU_MIN = 90      # риск-фильтр v2: без входа в последние 90 секунд окна
HOLD_DAYS = 30
CAP = 1500
READY_N = 200        # размеченных окон для статуса "готова"

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "pm-lab/1.0"})
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

def cleanup(lab):
    t_now = time.time()
    for k in list(lab.keys()):
        if not isinstance(lab.get(k), dict) or lab[k].get("end", 0) < t_now - HOLD_DAYS * 86400:
            lab.pop(k, None)
    by = {}
    for k, e in lab.items():
        by.setdefault(e.get("series", "?"), []).append((e.get("start", 0), k))
    for ser, lst in by.items():
        if len(lst) > CAP:
            lst.sort()
            for start, k in lst[: len(lst) - CAP]:
                lab.pop(k, None)

SERIES = [
    ("btc-up-or-down-5m", "BTCUSDT", 300, "sht5"),
    ("btc-up-or-down-15m", "BTCUSDT", 900, "sht15"),
    ("btc-up-or-down-1h", "BTCUSDT", 3600, "sht1h"),
    ("eth-up-or-down-5m", "ETHUSDT", 300, "sht5"),
    ("eth-up-or-down-15m", "ETHUSDT", 900, "sht15"),
    ("sol-up-or-down-5m", "SOLUSDT", 300, "sht5"),
]

def backfill(lab):
    """Бэкфилл истории: закрытые окна с официальным исходом из events API (без снимков)."""
    t0 = time.time()
    added = 0
    for series_slug, sym, win_s, tag in SERIES:
        if time.time() > t0 + BUDGET_S * 0.35: break
        for off in (0, 100):
            try:
                evs = fetch(GAMMA + "/events?series_slug=" + series_slug + "&closed=true&limit=100&offset=" + str(off) + "&order=endDate&ascending=false", 12)
            except Exception:
                break
            if not isinstance(evs, list) or not evs: break
            fresh = False
            for ev in evs:
                slug = ev.get("slug", "")
                mm = re.search(r"(\d{9,})$", slug)
                if not mm or slug in lab: continue
                src = ev if ev.get("outcomePrices") else (ev.get("markets") or [{}])[0]
                pr = [fnum(x) for x in plist(src.get("outcomePrices", "[]"))]
                if len(pr) != 2 or max(pr) < 0.99: continue
                start = int(mm.group(1))
                lab[slug] = {"series": series_slug, "tag": tag, "sym": sym, "win_s": win_s,
                             "start": start, "end": start + win_s, "snaps": [],
                             "outcome": 1 if pr[0] >= 0.5 else 0, "src": "hist"}
                added += 1
                fresh = True
            if not fresh: break
    return added

def settle(lab):
    """Разметка исходов: у закрытых окон берём реальный расчёт Polymarket."""
    t_now = time.time()
    done = 0
    cands = [(e["end"], k) for k, e in lab.items()
             if e.get("outcome") is None and e["end"] + 150 <= t_now]
    cands.sort()
    for end, k in cands:
        if done >= SETTLE_BATCH or time.time() > T0 + BUDGET_S * 0.5:
            break
        pr = None
        try:
            mk = fetch(GAMMA + "/markets?slug=" + k, 10)
            if isinstance(mk, list) and mk:
                pr = [fnum(x) for x in plist(mk[0].get("outcomePrices", "[]"))]
        except Exception:
            pr = None
        if not pr or len(pr) != 2 or max(pr) < 0.99:
            # закрытые окна удаляются из /markets - берём исход из /events
            try:
                ev = fetch(GAMMA + "/events?slug=" + k, 10)
                if isinstance(ev, list) and ev:
                    e0 = ev[0]
                    src = e0 if e0.get("outcomePrices") else (e0.get("markets") or [{}])[0]
                    pr = [fnum(x) for x in plist(src.get("outcomePrices", "[]"))]
            except Exception:
                pr = None
        if pr and len(pr) == 2 and max(pr) >= 0.99:
            lab[k]["outcome"] = 1 if pr[0] >= 0.5 else 0
            done += 1
    return done

def windows_sorted(lab):
    ws = [dict(e, slug=k) for k, e in lab.items() if e.get("outcome") is not None]
    ws.sort(key=lambda w: w["start"])
    return ws

def split_ids(ws):
    cut = int(len(ws) * SPLIT)
    return set(w["slug"] for w in ws[:cut])

def model_stats(ws, ids_is):
    """Последний снимок окна с fair = прогноз модели; рынок = цена в том же снимке."""
    per = {"is": [], "oos": []}
    per_tag = {}
    for w in ws:
        pred, mkt = None, None
        for s in w.get("snaps", []):
            if s[1] is not None and s[2] is not None:
                pred, mkt = s[2], s[1]
        if pred is None:
            continue
        up = w["outcome"]
        part = "is" if w["slug"] in ids_is else "oos"
        per[part].append((pred, mkt, up))
        per_tag.setdefault(w.get("tag", "?"), []).append((pred, mkt, up))
    def brier(lst, key):
        xs = [x for x in lst if x[key] is not None]
        if not xs: return None
        return round(sum((x[key] - x[2]) ** 2 for x in xs) / len(xs), 4)
    def acc(lst, key):
        xs = [x for x in lst if x[key] is not None]
        if not xs: return None
        return round(100.0 * sum((1 if x[key] >= 0.5 else 0) == x[2] for x in xs) / len(xs), 1)
    def bl(lst):
        xs = [x for x in lst if x[0] is not None and x[1] is not None]
        if not xs: return None
        return round(sum((((x[0] + x[1]) / 2.0) - x[2]) ** 2 for x in xs) / len(xs), 4)
    def fm(a, b):
        if a is None or b is None: return None
        return "мод " + str(a) + " / рынок " + str(b)
    return {
        "n": len(per["is"]) + len(per["oos"]),
        "is": {"n": len(per["is"]), "brier": brier(per["is"], 0), "acc": acc(per["is"], 0)},
        "oos": {"n": len(per["oos"]), "brier": brier(per["oos"], 0), "acc": acc(per["oos"], 0)},
        "mkt_cmp": {"is": fm(brier(per["is"], 0), brier(per["is"], 1)), "oos": fm(brier(per["oos"], 0), brier(per["oos"], 1))},
        "mkt_brier": {"is": brier(per["is"], 1), "oos": brier(per["oos"], 1)},
        "blend": {"is": bl(per["is"]), "oos": bl(per["oos"])},
        "per_tag": {t: {"n": len(v), "brier": brier(v, 0), "acc": acc(v, 0)} for t, v in sorted(per_tag.items())},
    }

def rule_trades(ws, ids_is):
    """Бэктест правила worker (эдж >= 5%): первая подходящая точка окна = сделка."""
    trades = []
    for w in ws:
        for s in sorted(w.get("snaps", []), key=lambda x: x[0]):
            t, p_up, fair, liq = s[0], s[1], s[2], s[3]
            if fair is None or p_up is None: continue
            tau = w["end"] - t
            if tau < TAU_MIN or tau > w.get("win_s", 1) * 0.8: continue
            edge_up = fair - p_up
            if edge_up >= EDGE_MIN: side, px = "Up", p_up
            elif edge_up <= -EDGE_MIN: side, px = "Down", 1.0 - p_up
            else: continue
            if px < PX_MIN or px > PX_MAX: continue
            if not liq or liq < MIN_LIQ: continue
            win = 1 if ((w["outcome"] == 1) == (side == "Up")) else 0
            pnl = (1.0 / (px + 0.005) - 1.0) if win else -1.0
            trades.append({"t": t, "tag": w.get("tag", "?"), "part": "is" if w["slug"] in ids_is else "oos",
                           "win": win, "pnl": round(pnl, 3), "px": round(px, 3)})
            break
    def agg(sub):
        if not sub: return {"n": 0, "winrate": None, "pnl_r": None, "ev": None}
        wr = sum(x["win"] for x in sub) / len(sub)
        pnl = sum(x["pnl"] for x in sub)
        return {"n": len(sub), "winrate": round(100 * wr, 1), "pnl_r": round(pnl, 2),
                "ev": round(pnl / len(sub), 3)}
    return {"is": agg([x for x in trades if x["part"] == "is"]),
            "oos": agg([x for x in trades if x["part"] == "oos"]),
            "total": agg(trades),
            "sample": sorted(trades, key=lambda x: -x["t"])[:12]}

def calib_bins(ws, ids_is):
    """Калибровка рынка: последний снимок окна, бины по цене Up, OOS-проверка."""
    rows = []
    for w in ws:
        sn = w.get("snaps", [])
        s = None
        for x in sn:
            if x[0] >= w["start"] and x[1] is not None:
                s = x
        if s is None: continue
        rows.append((s[1], w["outcome"], "is" if w["slug"] in ids_is else "oos"))
    bins = []
    for i in range(20):
        lo, hi = i / 20.0, (i + 1) / 20.0
        sub = [r for r in rows if lo <= r[0] < hi]
        if not sub: continue
        isub = [r for r in sub if r[2] == "is"]
        osub = [r for r in sub if r[2] == "oos"]
        mkt = sum(r[0] for r in sub) / len(sub)
        real = sum(r[1] for r in sub) / len(sub)
        real_is = (sum(r[1] for r in isub) / len(isub)) if len(isub) >= 15 else None
        real_oos = (sum(r[1] for r in osub) / len(osub)) if len(osub) >= 15 else None
        flag = False
        if len(sub) >= MIN_BIN and real_is is not None and real_oos is not None:
            d_is, d_oos = real_is - mkt, real_oos - mkt
            if abs(d_is) >= BIAS_EPS and abs(d_oos) >= BIAS_EPS * 0.6 and d_is * d_oos > 0:
                flag = True
        bins.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": len(sub),
                     "mkt": round(mkt, 3), "real": round(real, 3),
                     "is_real": None if real_is is None else round(real_is, 3),
                     "oos_real": None if real_oos is None else round(real_oos, 3),
                     "flag": flag})
    return bins

def streak_stats(ws):
    """P(Up) после k одинаковых исходов подряд; по теории ~50%."""
    by_tag = {}
    for w in ws:
        by_tag.setdefault(w.get("tag", "?"), []).append(w)
    out = []
    for tag in sorted(by_tag):
        lst = by_tag[tag]
        if len(lst) < 100: continue
        seq = [w["outcome"] for w in lst]
        for k in (1, 2, 3):
            up_n = up_w = dn_n = dn_w = 0
            for i in range(k, len(seq)):
                prev = seq[i - k:i]
                if all(x == 1 for x in prev):
                    up_n += 1; up_w += seq[i]
                elif all(x == 0 for x in prev):
                    dn_n += 1; dn_w += seq[i]
            pu = up_w / up_n if up_n >= 30 else None
            pd = dn_w / dn_n if dn_n >= 30 else None
            out.append({"tag": tag, "k": k, "n_up": up_n,
                        "p_after_up": None if pu is None else round(pu, 3),
                        "n_dn": dn_n, "p_after_dn": None if pd is None else round(pd, 3)})
    return out

def streak_edge(ws):
    """Walk-forward тест стрик-эджа: вероятности момента оцениваем на старых 70%,
    сделки правила проверяем ТОЛЬКО на новых 30% (чистый OOS, без утечек)."""
    ws = sorted(ws, key=lambda w: w["start"])
    cut = int(len(ws) * SPLIT)
    old, new = ws[:cut], ws[cut:]
    by = {}
    for w in ws:
        by.setdefault(w["series"], []).append((w["start"], w["outcome"]))
    for s in by: by[s].sort()
    prev = {}
    for s, lst in by.items():
        for i in range(1, len(lst)):
            prev[lst[i][0]] = lst[i - 1][1]
    cnt = {1: [0, 0], 0: [0, 0]}
    for w in old:
        pv = prev.get(w["start"])
        if pv is None: continue
        cnt[pv][1] += 1
        cnt[pv][0] += w["outcome"]
    pu = (cnt[1][0] / cnt[1][1]) if cnt[1][1] >= 50 else None
    pd = (cnt[0][0] / cnt[0][1]) if cnt[0][1] >= 50 else None
    trades = []
    if pu is not None and pd is not None:
        for w in new:
            if not w.get("snaps"): continue
            pv = prev.get(w["start"])
            if pv is None: continue
            for s in sorted(w["snaps"], key=lambda x: x[0]):
                t, p_up = s[0], s[1]
                if p_up is None: continue
                tau = w["end"] - t
                if tau < TAU_MIN or tau > w.get("win_s", 1) * 0.8: continue
                side, px = None, 0.0
                if pv == 1 and pu - p_up >= EDGE_MIN: side, px = "Up", p_up
                elif pv == 0 and p_up - pd >= EDGE_MIN: side, px = "Down", 1.0 - p_up
                if side is None: continue
                if px < PX_MIN or px > PX_MAX: continue
                if not s[3] or s[3] < MIN_LIQ: continue
                win = 1 if ((w["outcome"] == 1) == (side == "Up")) else 0
                pnl = (1.0 / (px + 0.005) - 1.0) if win else -1.0
                trades.append({"t": t, "win": win, "pnl": round(pnl, 3), "px": round(px, 3), "tag": w.get("tag", "?")})
                break
    def agg(sub):
        if not sub: return {"n": 0, "winrate": None, "ev": None}
        pnl = sum(x["pnl"] for x in sub)
        return {"n": len(sub), "winrate": round(100.0 * sum(x["win"] for x in sub) / len(sub), 1), "ev": round(pnl / len(sub), 3)}
    return {"probs": {"after_up": None if pu is None else round(pu, 3), "after_down": None if pd is None else round(pd, 3)},
            "n_est": {"up": cnt[1][1], "down": cnt[0][1]},
            "oos": agg(trades), "trades": trades[-12:]}

def hour_stats(ws):
    """Доля Up по часам UTC: гипотеза - время суток не влияет."""
    by = {}
    for w in ws:
        by.setdefault(time.gmtime(w["start"]).tm_hour, []).append(w["outcome"])
    base = (sum(w["outcome"] for w in ws) / len(ws)) if ws else 0.5
    out = []
    for h in sorted(by):
        if len(by[h]) >= 50:
            r = sum(by[h]) / len(by[h])
            out.append({"hour": h, "n": len(by[h]), "up_rate": round(r, 3), "flag": abs(r - base) >= 0.10})
    return out, round(base, 3)

def hyps(model, rule, bins, stk, ws, hours, se):
    H = []
    def add(name, note, is_n, is_v, oos_n, oos_v, ok, collecting):
        status = "⏳ копим данные" if collecting else ("✅ подтверждено OOS" if ok else "❌ отвергнуто OOS")
        H.append({"name": name, "note": note, "is_n": is_n, "is_v": is_v,
                  "oos_n": oos_n, "oos_v": oos_v, "ok": ok, "status": status})
    oos_n = model["oos"]["n"]
    # H1: модель точнее монетки
    ok1 = model["is"]["brier"] is not None and model["is"]["brier"] < 0.25 and \
          model["oos"]["brier"] is not None and model["oos"]["brier"] < 0.25 and oos_n >= MIN_OOS
    add("Модель точнее монетки", "Brier < 0.25 на обеих половинах",
        model["is"]["n"], model["is"]["brier"], oos_n, model["oos"]["brier"],
        ok1, model["oos"]["n"] < MIN_OOS or model["is"]["n"] < 30)
    # H2: модель не хуже рынка
    mb_is = model["mkt_brier"]["is"]
    mb_oos = model["mkt_brier"]["oos"]
    ok2 = model["is"]["brier"] is not None and model["oos"]["brier"] is not None and \
          mb_is is not None and mb_oos is not None and \
          model["is"]["brier"] <= mb_is and model["oos"]["brier"] <= mb_oos and oos_n >= MIN_OOS
    add("Модель конкурирует с рынком", "Brier модель vs рынок (тот же снимок)",
        model["is"]["n"], model["mkt_cmp"]["is"], oos_n, model["mkt_cmp"]["oos"],
        ok2, oos_n < MIN_OOS)
    # H3: правило эджа с положительным EV
    ris, roos = rule["is"], rule["oos"]
    ok3 = ris["ev"] is not None and ris["ev"] > 0 and roos["ev"] is not None and roos["ev"] > 0 and roos["n"] >= MIN_OOS
    add("Правило «эдж ≥ 5%» с положительным EV", "бэктест по снимкам, 1 сделка на окно",
        ris["n"], ("EV " + str(ris["ev"]) + " R") if ris["ev"] is not None else None,
        roos["n"], ("EV " + str(roos["ev"]) + " R") if roos["ev"] is not None else None,
        ok3, roos["n"] < MIN_OOS)
    # H4: есть системный сдвиг в каком-то ценовом бине
    flagged = [b for b in bins if b["flag"]]
    names = ", ".join("%d-%d%%" % (b["lo"] * 100, b["hi"] * 100) for b in flagged[:3])
    ok4 = len(flagged) > 0
    add("Есть бин рынка с системной ошибкой", "сдвиг >= 5пп на IS и OOS" + (": " + names if names else ""),
        len(bins), "флагов: " + str(len(flagged)),
        len(flagged), "бинов с флагом: " + str(len(flagged)),
        ok4, sum(b["n"] for b in bins) < MIN_BIN * 4)
    # H5: стрики не дают эджа
    big = [s for s in stk if s["p_after_up"] is not None or s["p_after_dn"] is not None]
    dev = [s for s in big if (s["p_after_up"] is not None and abs(s["p_after_up"] - 0.5) >= STK_DEV) or
                              (s["p_after_dn"] is not None and abs(s["p_after_dn"] - 0.5) >= STK_DEV)]
    ok5 = not dev
    add("Стрики не дают эджа (эффективность)", "P(Up) после серии k в пределах 50±10%",
        len(big), "уклонений: " + str(len(dev)), len(big), "уклонений: " + str(len(dev)),
        ok5, len(ws) < 300)
    if not ok5 and len(ws) >= 300:
        H[-1]["status"] = "✨ паттерн: стрики влияют на исход"
    # H6: смесь модели и рынка бьёт обоих
    b_is, b_oos = model["blend"]["is"], model["blend"]["oos"]
    vals = (b_is, b_oos, model["is"]["brier"], model["mkt_brier"]["is"], model["oos"]["brier"], model["mkt_brier"]["oos"])
    ok6 = None not in vals and oos_n >= MIN_OOS and \
        b_is < min(model["is"]["brier"], model["mkt_brier"]["is"]) and \
        b_oos < min(model["oos"]["brier"], model["mkt_brier"]["oos"])
    add("Смесь 50/50 модели и рынка бьёт обоих", "Brier смеси < min(модель, рынок) на обеих половинах",
        model["is"]["n"], b_is, oos_n, b_oos, ok6, oos_n < MIN_OOS)
    # H7: время суток не влияет на исход
    flagged_h = [x for x in hours if x["flag"]]
    ok7 = not flagged_h
    names_h = ", ".join(str(x["hour"]) + ":00 UTC" for x in flagged_h[:3])
    add("Время суток не влияет на исход Up/Down", "уклонения >= 10пп от базы при n >= 50 в часе" + (": " + names_h if names_h else ""),
        len(hours), "уклонений: " + str(len(flagged_h)),
        len(hours), "уклонений: " + str(len(flagged_h)),
        ok7, len(ws) < 300)
    if not ok7 and len(ws) >= 300:
        H[-1]["status"] = "✨ паттерн: время суток влияет"
    # H8: стрик-эдж - рынок недооценивает моментум (walk-forward)
    so = se["oos"]
    ok8 = so["ev"] is not None and so["ev"] > 0 and so["n"] >= MIN_OOS
    add("Стрик-эдж: рынок недооценивает моментум", "вероятности из старых 70% данных, сделки только на новых 30%; P(Up|после Up)=" + str(se["probs"]["after_up"]) + ", P(Up|после Down)=" + str(se["probs"]["after_down"]),
        se["n_est"]["up"] + se["n_est"]["down"], "P(up|up)=" + str(se["probs"]["after_up"]),
        so["n"], so["ev"], ok8, so["n"] < MIN_OOS)
    return H

def main():
    lab = jload(SRC, {})
    if not isinstance(lab, dict): lab = {}
    cleanup(lab)
    bf = backfill(lab)
    done = settle(lab)
    ws = windows_sorted(lab)
    ws_snap = [w for w in ws if w.get("snaps")]
    ids_is = split_ids(ws_snap)
    model = model_stats(ws_snap, ids_is)
    rule = rule_trades(ws_snap, ids_is)
    bins = calib_bins(ws_snap, ids_is)
    stk = streak_stats(ws)
    hours, base_up = hour_stats(ws)
    se = streak_edge(ws)
    H = hyps(model, rule, bins, stk, ws, hours, se)
    h8 = [h for h in H if h["name"].startswith("Стрик-эдж")]
    h8s = h8[0]["status"] if h8 else "⏳ копим данные"
    settled = len(ws)
    snaps = sum(len(e.get("snaps", [])) for e in lab.values())
    span_h = round((max(w["start"] for w in ws) - min(w["start"] for w in ws)) / 3600.0, 1) if ws else 0
    golden = [h for h in H if h["ok"] and h["status"].startswith("✅")]
    if settled < READY_N:
        note = "Размечено " + str(settled) + " окон из " + str(len(lab)) + " в базе. Надёжные выводы с ~" + str(READY_N) + " размеченных окон (сейчас " + str(round(100.0 * settled / READY_N)) + "% пути). Правила зафиксированы заранее - подгонки нет."
    else:
        note = "База достаточна. Подтверждённых OOS закономерностей: " + str(len(golden)) + " из " + str(len(H)) + " гипотез."
    out = {
        "updated": now(),
        "status": "ready" if settled >= READY_N else "collecting",
        "status_note": note,
        "settled_this_run": done, "backfilled": bf, "base_up_rate": base_up,
        "data": {"windows": len(lab), "settled": settled, "snaps": snaps, "span_h": span_h,
                 "hist": sum(1 for e in lab.values() if e.get("src") == "hist")},
        "model": model,
        "rule": rule,
        "bins": bins,
        "streaks": stk,
        "hours": hours,
        "streak_edge": se,
        "hyp": H,
        "golden": [{"name": g["name"], "note": g["note"]} for g in golden],
    }
    jsave(SRC, lab)
    jsave(OUT, out)
    h3 = [h for h in H if h["name"].startswith("Правило")]
    jsave("results/strategies.json", {"updated": now(),
        "rule_v2": {"status": h3[0]["status"] if h3 else "⏳ копим данные", "ev_oos": rule["oos"]["ev"], "n_oos": rule["oos"]["n"]},
        "golden": [g["name"] for g in golden],
        "patterns": [h["name"] + " | " + h["note"] for h in H if h["status"].startswith("✨")],
        "streak_v1": {"status": h8s, "probs": se["probs"], "oos": se["oos"]}})
    print("lab: windows %d, settled %d (+%d), snaps %d, hyps %d, golden %d" % (
        len(lab), settled, done, snaps, len(H), len(golden)))

if __name__ == "__main__":
    main()
