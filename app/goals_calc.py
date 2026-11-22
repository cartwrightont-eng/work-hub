"""Goal maths, kept pure so it can be tested by hand.
  key result progress = (current - start) / (target - start), clamped to 0..1
  objective progress  = average of its key results AND the progress of the goals directly below it
                        (so a personal key result moves the team goal, which moves the company goal)
  status (derived)    = progress vs share of the cycle elapsed: gap <= 10 pts on track, <= 25 at risk, else off track"""
from statistics import mean

ON_TRACK_GAP, AT_RISK_GAP = 0.10, 0.25

def kr_progress(start, target, current):
    s, t, c = float(start), float(target), float(current)
    if t == s: return 1.0 if c >= t else 0.0
    return max(0.0, min(1.0, (c - s) / (t - s)))

def elapsed_share(start, end, as_of):
    return max(0.0, min(1.0, ((as_of - start).days + 1) / ((end - start).days + 1)))

def status_for(progress, elapsed):
    if progress is None: return "no_data"
    gap = elapsed - progress
    return "on_track" if gap <= ON_TRACK_GAP + 1e-9 else "at_risk" if gap <= AT_RISK_GAP + 1e-9 else "off_track"

def compute(objs, krs, as_of):
    kids, by, memo = {}, {}, {}
    for o in objs: kids.setdefault(o["parent_id"], []).append(o["id"])
    for k in krs: by.setdefault(k["objective_id"], []).append(k)
    def prog(oid):
        if oid not in memo:
            items = [kr_progress(k["start_value"], k["target"], k["current"]) for k in by.get(oid, [])]
            items += [p for p in (prog(c) for c in kids.get(oid, [])) if p is not None]
            memo[oid] = mean(items) if items else None
        return memo[oid]
    out = {}
    for o in objs:
        p, el = prog(o["id"]), elapsed_share(o["cycle_start"], o["cycle_end"], as_of)
        out[o["id"]] = {"progress": None if p is None else round(p, 4), "elapsed": round(el, 4), "status": status_for(p, el)}
    return out
