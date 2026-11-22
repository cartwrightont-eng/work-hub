"""The four scorecard metrics. Weeks run Monday to Sunday in the organisation timezone.
Definitions (all from v_deliverable_facts):
  on-time rate : done on or before ORIGINAL deadline / deliverables whose deadline fell in the period
                 (not-yet-due, unfinished items are left out until their deadline passes)
  work-rate    : points completed per week (by completion week), plus 4-week rolling average
  cycle time   : median days, latest start to done, bucketed by completion week
  rework rate  : completed deliverables that were ever reopened / completed, bucketed by completion month"""
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

def wk(d): return d - timedelta(days=d.weekday())
def _rate(a, b): return round(a / b, 4) if b else None
def _done(f, as_of): return f["done_local_date"] is not None and f["done_local_date"] <= as_of
def _ids(fs): return [f["id"] for f in fs]

def _ontime(facts, as_of, s, e):
    due = [f for f in facts if s <= f["original_deadline"] <= e and (_done(f, as_of) or f["original_deadline"] < as_of)]
    ok = [f for f in due if _done(f, as_of) and f["done_local_date"] <= f["original_deadline"]]
    return len(ok), len(due), _ids(due)

def _completed(facts, as_of, s, e):
    return [f for f in facts if _done(f, as_of) and s <= f["done_local_date"] <= e]

def _median(done):
    c = [float(f["cycle_days"]) for f in done if f["cycle_days"] is not None]
    return (round(median(c), 3) if c else None), len(c)

def summarise(facts, as_of, s, e):
    ok, n, _ = _ontime(facts, as_of, s, e)
    done, weeks = _completed(facts, as_of, s, e), ((e - s).days + 1) / 7
    med, nc = _median(done)
    return {"on_time": {"rate": _rate(ok, n), "n": n},
            "work_rate": {"points_per_week": round(sum(f["points"] or 0 for f in done) / weeks, 2),
                          "tasks_per_week": round(len(done) / weeks, 2), "n": len(done)},
            "cycle_time": {"median_days": med, "n": nc},
            "rework": {"rate": _rate(sum(1 for f in done if f["reopen_count"] > 0), len(done)), "n": len(done)}}

def build(facts, as_of, weeks=12):
    end = wk(as_of)
    starts = [end - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    on_time, work, cycle = [], [], []
    for s in starts:
        e = s + timedelta(days=6)
        ok, n, ids = _ontime(facts, as_of, s, e)
        on_time.append({"week": s.isoformat(), "rate": _rate(ok, n), "n": n, "ids": ids})
        done = _completed(facts, as_of, s, e)
        work.append({"week": s.isoformat(), "points": sum(f["points"] or 0 for f in done),
                     "tasks": len(done), "ids": _ids(done)})
        med, nc = _median(done)
        cycle.append({"week": s.isoformat(), "median_days": med, "n": nc, "ids": _ids(done)})
    for i, w in enumerate(work):
        w["rolling"] = round(sum(x["points"] for x in work[i - 3:i + 1]) / 4, 2) if i >= 3 else None
    months, m = [], date(starts[0].year, starts[0].month, 1)
    while m <= as_of:
        months.append(m); m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    rework = []
    for m in months:
        done = [f for f in facts if _done(f, as_of) and (f["done_local_date"].year, f["done_local_date"].month) == (m.year, m.month)]
        bad = [f for f in done if f["reopen_count"] > 0]
        rework.append({"month": m.strftime("%Y-%m"), "rate": _rate(len(bad), len(done)), "n": len(done),
                       "reopened": len(bad), "ids": _ids(done)})
    cs = end - timedelta(weeks=3)
    cur, prev = summarise(facts, as_of, cs, as_of), summarise(facts, as_of, cs - timedelta(days=28), cs - timedelta(days=1))
    return {"as_of": as_of.isoformat(), "tiles": cur, "previous": prev,
            "on_time": on_time, "work_rate": work, "cycle_time": cycle, "rework": rework}

def scorecard_for(cur, org, person_id, as_of=None, weeks=12):
    cur.execute("SELECT value #>> '{}' AS tz FROM organisation_settings WHERE org_id=%s AND key='timezone'", (org,))
    r = cur.fetchone()
    as_of = as_of or datetime.now(ZoneInfo(r["tz"] if r else "Africa/Nairobi")).date()
    cur.execute("SELECT * FROM v_deliverable_facts WHERE org_id=%s AND owner_id=%s ORDER BY id", (org, person_id))
    return build(cur.fetchall(), as_of, weeks)

def org_today(cur, org):
    cur.execute("SELECT value #>> '{}' AS tz FROM organisation_settings WHERE org_id=%s AND key='timezone'", (org,))
    r = cur.fetchone()
    return datetime.now(ZoneInfo(r["tz"] if r else "Africa/Nairobi")).date()

def compare(facts, as_of, weeks):
    """Current period vs the equally long period before it, for one person or one team.
    Only on-time and rework rates are returned: no points, so nothing here ranks people by volume."""
    cs = wk(as_of) - timedelta(weeks=weeks - 1)
    cur = summarise(facts, as_of, cs, as_of)
    prev = summarise(facts, as_of, cs - timedelta(weeks=weeks), cs - timedelta(days=1))
    def pack(k):
        a, b = cur[k]["rate"], prev[k]["rate"]
        return {"rate": a, "n": cur[k]["n"], "change_pts": round((a - b) * 100, 1) if a is not None and b is not None else None}
    return {"on_time": pack("on_time"), "rework": pack("rework")}
