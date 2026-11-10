# OmniLab reporter v1.1.0
"""OmniLab — единый репортёр: агрегирует статус всех движков в results/universe.json.

Бог-обзор: Polymarket live-трейдинг, CLOB-трейдер, исследовательская лаборатория,
walk-forward бэктесты, OKX live-трейдинг — в одном файле для дашборда.
Запускается после каждого движка. Без зависимостей.
"""
import json, os, time, calendar

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

def ts(iso):
    try:
        return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0

def age(iso):
    return round(time.time() - ts(iso), 0) if iso else None

def main():
    st = jload("results/state.json", {})
    stats = st.get("stats", {}) if isinstance(st, dict) else {}
    sht = jload("results/sht.json", {})
    cal = sht.get("cal", {}) if isinstance(sht, dict) else {}
    lab = jload("results/lab.json", {})
    ldata = lab.get("data", {}) if isinstance(lab, dict) else {}
    prog = jload("engines/scalp/results/progress.json", {})
    paper = jload("engines/scalp/results/paper.json", {})
    okx = jload("engines/okx/results/okx_live.json", {})
    clob = jload("results/clob.json", {})

    pm = {
        "equity": stats.get("equity"), "pnl_pct": stats.get("pnl_pct"),
        "trades": stats.get("trades"), "winrate": stats.get("winrate"),
        "open": stats.get("open"), "sht_open": stats.get("sht_open"),
        "sht_trades": stats.get("sht_trades"), "sht_pnl": stats.get("sht_pnl"),
        "cal": cal, "updated": stats.get("updated"), "age_s": age(stats.get("updated")),
    }
    labm = {
        "status": lab.get("status"), "status_note": lab.get("status_note"),
        "win
dows": ldata.get("windows"), "settled": ldata.get("settled"),
        "snaps": ldata.get("snaps"), "golden": [g.get("name") for g in (lab.get("golden") or [])],
        "updated": lab.get("updated"), "age_s": age(lab.get("updated")),
    }
    lb_rows = []
    try:
        with open("engines/scalp/reports/leaderboard.csv") as f:
            head = f.readline().strip().split(",")
            for line in f:
                p = line.strip().split(",")
                if len(p) == len(head):
                    lb_rows.append(dict(zip(head, p)))
                if len(lb_rows) >= 15:
                    break
    except Exception:
        pass
    pstats = paper.get("stats", {}) if isinstance(paper, dict) else {}
    scalp = {
        "done": prog.get("done"), "total": prog.get("total"),
        "errors": prog.get("errors"), "pending": prog.get("pending"),
        "updated": prog.get("updated"), "age_s": age(prog.get("updated")),
        "paper": {
            "equity": pstats.get("equity"), "pnl_pct": pstats.get("pnl_pct"),
            "trades": pstats.get("n_trades"), "winrate": pstats.get("winrate"),
            "open": pstats.get("open"), "tops": len(paper.get("tops", []) or []),
        },
        "paper_updated": paper.get("updated") if isinstance(paper, dict) else None,
        "leaderboard": lb_rows,
    }
    okxm = {
        "mode": okx.get("mode"), "demo": okx.get("demo"),
        "equity": okx.get("equity"), "pnl_pct": okx.get("pnl_pct"),
        "trades": okx.get("trades"), "open": okx.get("open"),
        "signals": okx.get("signals"),
        "paper_positions": (okx.get("paper") or {}).get("positions", {}),
        "live_positions": (okx.get("live") or {}).get("positions", {}),
        "errors": okx.get("errors"),
        "updated": okx.get("updated"), "age_s": age(okx.get("updated")),
    }
    clobm = {
        "mode": clob.get("mode"), "note": clob.get("note"), "picked": clob.get("picked"),
        "order": clob.get("order"), "error": clob.get("error")
,
        "updated": clob.get("updated"), "age_s": age(clob.get("updated")),
    }
    health = {}
    health["pm_worker"] = "ok" if (pm["age_s"] is not None and pm["age_s"] < 900) else "stale"
    health["pm_lab"] = "ok" if (labm["age_s"] is not None and labm["age_s"] < 7200) else "stale"
    health["scalp_worker"] = "ok" if (scalp["age_s"] is not None and scalp["age_s"] < 5400) else ("warmup" if not prog else "stale")
    health["okx_trader"] = "ok" if (okxm["age_s"] is not None and okxm["age_s"] < 1500) else ("warmup" if not okx else "stale")
    health["pm_trader"] = "ok" if (clobm["age_s"] is not None and clobm["age_s"] < 1500) else ("warmup" if not clob else "stale")
    overall = "ok" if all(v == "ok" for v in health.values()) else "degraded"
    uni = {
        "updated": now(),
        "health": {"overall": overall, "modules": health},
        "pm": pm, "pm_lab": labm, "scalp": scalp,
        "okx": okxm, "clob": clobm,
    }
    jsave("results/universe.json", uni)
    print("universe: pm %s, scalp %s/%s, okx %s (%s), clob %s, health %s" % (
        pm.get("equity"), scalp.get("done"), scalp.get("total"),
        okxm.get("mode"), okxm.get("equity"), clobm.get("mode"), overall))

if __name__ == "__main__":
    main()
