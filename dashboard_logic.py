"""Pure calculations for the dashboard: consistency, patterns, habits, tasks, check-in health and insights.

Nothing here touches the database or the clock (callers pass `now`), so it is easy to test.

Conventions, matching the rest of the app:
- Days are the user's local days. "Today" never counts against you until it is over.
- An *active day* is any day with at least one action: a check-in, a post, a habit commit,
  or a task marked done.
- Insights are rule-based, need a minimum amount of data, and are worded kindly.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from habit_logic import is_due, streaks
from todo_logic import safe_zone

RANGES = (7, 30, 90)
DEFAULT_RANGE = 30
HEATMAP_DAYS = 91            # 13 weeks
HISTORY_DAYS = 270           # how far back the caller should load data (covers the previous window)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BUCKETS = (("Morning", 5, 12), ("Afternoon", 12, 17), ("Evening", 17, 22), ("Night", 22, 5))
BUCKET_PHRASE = {"Morning": "in the morning", "Afternoon": "in the afternoon", "Evening": "in the evening", "Night": "late at night"}
MAX_INSIGHTS = 7


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


def _clip(text, n=40):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _aware(value):
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bucket(hour):
    for name, start, end in BUCKETS:
        if start < end and start <= hour < end:
            return name
        if start > end and (hour >= start or hour < end):
            return name
    return "Night"


def _hours_text(hours):
    if hours < 48:
        return f"{round(hours)} h"
    return f"{round(hours / 24, 1)} days"


def _label(score):
    if score is None:
        return "No data yet"
    if score >= 80:
        return "Rock solid"
    if score >= 60:
        return "Steady"
    if score >= 40:
        return "Building"
    return "Getting started"


def _habit_start(habit, done_days):
    return min([date.fromisoformat(habit["created_day"])] + [date.fromisoformat(d) for d in done_days])


def _count_range(mask, done_days, first_day, s, e, today):
    """(done, due) scheduled days in [s, e]. Today only counts once it is done."""
    d, done, due = max(s, first_day), 0, 0
    while d <= e:
        if is_due(mask, d):
            finished = d.isoformat() in done_days
            if d < today or finished:
                due += 1
                done += 1 if finished else 0
        d += timedelta(days=1)
    return done, due


def _bucket_summary(counter):
    total = sum(counter.values())
    return {
        "total": total,
        "buckets": [{"name": name, "count": counter.get(name, 0), "pct": _pct(counter.get(name, 0), total) or 0} for name, _, _ in BUCKETS],
    }


def _top_bucket(summary):
    if not summary["total"]:
        return None
    best = max(summary["buckets"], key=lambda b: b["count"])
    return best


def build_dashboard(*, now, today_iso, tz_name, range_days, habits, habit_logs, tasks, activity, deadline_hours):
    """Everything the dashboard page shows.

    habits:     rows of {id, name, icon, color, days, created_day}
    habit_logs: rows of {habit_id, day, note, created_at}
    tasks:      rows of {id, title, day, priority, created_at, done_at, postponed}
    activity:   rows of {timestamp, category} (posts and check-ins; category 'pulse' = hold check-in)
    """
    tz = safe_zone(tz_name)
    today = date.fromisoformat(today_iso)
    n = range_days
    start = today - timedelta(days=n - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=n - 1)

    # ---- gather every action with its local day (and local time where known) ----
    per_day = Counter()
    timed = Counter()          # time-of-day bucket -> actions inside the window
    task_timed = Counter()     # same, for finished tasks only
    pulse_days, stamps = set(), []
    pulses = posts = 0

    def note_time(local_dt, into=None):
        if start <= local_dt.date() <= today:
            timed[_bucket(local_dt.hour)] += 1
            if into is not None:
                into[_bucket(local_dt.hour)] += 1

    for row in activity:
        utc = _aware(row["timestamp"])
        local = utc.astimezone(tz)
        stamps.append(utc)
        per_day[local.date()] += 1
        note_time(local)
        if row["category"] == "pulse":
            if start <= local.date() <= today:
                pulses += 1
                pulse_days.add(local.date())
        elif start <= local.date() <= today:
            posts += 1

    habit_done = {}
    for row in habit_logs:
        habit_done.setdefault(row["habit_id"], set()).add(row["day"])
        day = date.fromisoformat(row["day"])
        per_day[day] += 1
        created = row.get("created_at")
        if created:
            local = _aware(created).astimezone(tz)
            if local.date() == day:        # skip backfilled commits: their time says nothing about when you act
                note_time(local)

    done_by_day = Counter()
    for t in tasks:
        if t.get("done_at"):
            local = _aware(t["done_at"]).astimezone(tz)
            per_day[local.date()] += 1
            done_by_day[local.date()] += 1
            note_time(local, task_timed)

    stamps.sort()
    all_days = [d for d, c in per_day.items() if c > 0]
    first = min(all_days) if all_days else None

    # ---- consistency ----
    def active_in(s, e):
        """(active days, days counted) in [s, e], from the first action on; an empty today isn't counted."""
        if first is None:
            return 0, 0
        s = max(s, first)
        if e == today and per_day.get(today, 0) == 0:
            e = e - timedelta(days=1)
        if e < s:
            return 0, 0
        days = (e - s).days + 1
        return sum(1 for i in range(days) if per_day.get(s + timedelta(days=i), 0) > 0), days

    active, counted = active_in(start, today)
    score = _pct(active, counted)
    p_active, p_counted = active_in(prev_start, prev_end)
    prev_score = _pct(p_active, p_counted) if p_counted >= 5 else None
    delta = score - prev_score if (score is not None and prev_score is not None) else None

    run = best_run = 0
    if first is not None:
        d = first
        while d <= today:
            if per_day.get(d, 0) > 0:
                run += 1
                best_run = max(best_run, run)
            elif d != today:
                run = 0
            d += timedelta(days=1)

    consistency = {
        "score": score,
        "label": _label(score),
        "active_days": active,
        "window_days": counted,
        "prev_score": prev_score,
        "delta": delta,
        "streak": run,
        "best_streak": best_run,
        "days_tracked": (today - first).days + 1 if first else 0,
    }
    heatmap = [{"date": (today - timedelta(days=i)).isoformat(), "count": per_day.get(today - timedelta(days=i), 0)}
               for i in range(HEATMAP_DAYS - 1, -1, -1)]

    # ---- weekday pattern ----
    weekdays = []
    for wd in range(7):
        days = act = actions = 0
        if first is not None:
            s = max(start, first)
            e = today if per_day.get(today, 0) > 0 else today - timedelta(days=1)
            d = s
            while d <= e:
                if d.weekday() == wd:
                    days += 1
                    actions += per_day.get(d, 0)
                    act += 1 if per_day.get(d, 0) > 0 else 0
                d += timedelta(days=1)
        weekdays.append({"day": WEEKDAYS[wd][:3], "days": days, "active": act, "rate": _pct(act, days), "actions": actions})
    time_of_day = _bucket_summary(timed)

    # ---- habits ----
    habit_rows, tot_done, tot_due, ptot_done, ptot_due = [], 0, 0, 0, 0
    wk_done, wk_due = [0] * 7, [0] * 7
    for h in habits:
        done_days = habit_done.get(h["id"], set())
        hs = _habit_start(h, done_days)
        cur, best = streaks(h["days"], done_days, hs, today)
        done_n, due_n = _count_range(h["days"], done_days, hs, start, today, today)
        if hs <= prev_end:
            pd_, pu_ = _count_range(h["days"], done_days, hs, prev_start, prev_end, today)
            ptot_done += pd_
            ptot_due += pu_
        tot_done += done_n
        tot_due += due_n
        d = max(start, hs)
        while d <= today:
            if is_due(h["days"], d):
                finished = d.isoformat() in done_days
                if d < today or finished:
                    wk_due[d.weekday()] += 1
                    wk_done[d.weekday()] += 1 if finished else 0
            d += timedelta(days=1)
        due_today = is_due(h["days"], today)
        done_today = today_iso in done_days
        habit_rows.append({
            "id": h["id"], "name": h["name"], "icon": h["icon"], "color": h["color"],
            "rate": _pct(done_n, due_n), "done": done_n, "due": due_n,
            "streak": cur, "best_streak": best,
            "due_today": due_today, "done_today": done_today,
            "at_risk": bool(due_today and not done_today and cur >= 3),
        })
    habit_rows.sort(key=lambda r: (-(r["rate"] if r["rate"] is not None else -1), r["name"].lower()))
    h_rate = _pct(tot_done, tot_due)
    h_prev = _pct(ptot_done, ptot_due) if ptot_due >= 5 else None
    habit_section = {
        "count": len(habit_rows),
        "rate": h_rate,
        "done": tot_done,
        "due": tot_due,
        "prev_rate": h_prev,
        "delta": h_rate - h_prev if (h_rate is not None and h_prev is not None) else None,
        "active_streaks": sum(1 for r in habit_rows if r["streak"] > 0),
        "best_streak": max([r["best_streak"] for r in habit_rows], default=0),
        "weekdays": [{"day": WEEKDAYS[i][:3], "due": wk_due[i], "done": wk_done[i], "rate": _pct(wk_done[i], wk_due[i])} for i in range(7)],
        "habits": habit_rows,
    }

    # ---- tasks ----
    def task_stats(s, e):
        """Tasks planned for [s, e]. A task planned for today only counts once done."""
        rows = [t for t in tasks if s <= date.fromisoformat(t["day"]) <= e and (date.fromisoformat(t["day"]) < today or t.get("done_at"))]
        done = [t for t in rows if t.get("done_at")]
        return rows, done

    rows, done_rows = task_stats(start, today)
    on_time = late = 0
    for t in done_rows:
        finished_day = _aware(t["done_at"]).astimezone(tz).date()
        if finished_day <= date.fromisoformat(t["day"]):
            on_time += 1
        else:
            late += 1
    in_window = [t for t in tasks if start <= date.fromisoformat(t["day"]) <= today]
    # Postponements include tasks you just pushed to tomorrow, so look at everything from the window onwards.
    pushed = [t for t in tasks if date.fromisoformat(t["day"]) >= start]
    postponed_events = sum(int(t.get("postponed") or 0) for t in pushed)
    postponed_tasks = sum(1 for t in pushed if int(t.get("postponed") or 0) > 0)
    most = max(pushed, key=lambda t: int(t.get("postponed") or 0), default=None)
    most_postponed = ({"title": _clip(most["title"]), "times": int(most["postponed"])} if most and int(most.get("postponed") or 0) >= 2 else None)
    open_overdue = sum(1 for t in in_window if date.fromisoformat(t["day"]) < today and not t.get("done_at"))
    finished_in_window = sum(c for d, c in done_by_day.items() if start <= d <= today)
    tw_planned, tw_done = [0] * 7, [0] * 7
    for t in rows:
        w = date.fromisoformat(t["day"]).weekday()
        tw_planned[w] += 1
        tw_done[w] += 1 if t.get("done_at") else 0
    p_rows, p_done = task_stats(prev_start, prev_end)
    t_rate = _pct(len(done_rows), len(rows))
    t_prev = _pct(len(p_done), len(p_rows)) if len(p_rows) >= 5 else None
    task_section = {
        "planned": len(rows),
        "done": len(done_rows),
        "rate": t_rate,
        "prev_rate": t_prev,
        "delta": t_rate - t_prev if (t_rate is not None and t_prev is not None) else None,
        "on_time": on_time,
        "late": late,
        "postponed_events": postponed_events,
        "postponed_tasks": postponed_tasks,
        "most_postponed": most_postponed,
        "open_overdue": open_overdue,
        "avg_planned_per_day": round(len(rows) / max(1, counted or n), 1),
        "avg_done_per_day": round(finished_in_window / max(1, counted or n), 1),
        "weekdays": [{"day": WEEKDAYS[i][:3], "planned": tw_planned[i], "done": tw_done[i], "rate": _pct(tw_done[i], tw_planned[i])} for i in range(7)],
        "time_of_day": _bucket_summary(task_timed),
    }

    # ---- check-in health (every post and check-in resets the deadline) ----
    window_start = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(stamps, stamps[1:]) if b >= window_start]
    if stamps:
        gaps.append((now - stamps[-1]).total_seconds() / 3600)   # the silence you're in right now
    longest = max(gaps) if gaps else None
    checkins = {
        "pulse_days": len(pulse_days),
        "pulses": pulses,
        "posts": posts,
        "deadline_hours": deadline_hours,
        "avg_gap_hours": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "longest_gap_hours": round(longest, 1) if longest is not None else None,
        "longest_pct": _pct(longest, deadline_hours) if longest is not None else None,
        "past_deadline": sum(1 for g in gaps if g > deadline_hours),
        "window_days": counted,
    }

    data = {
        "range": n,
        "today": today_iso,
        "timezone": tz_name,
        "consistency": consistency,
        "heatmap": heatmap,
        "patterns": {"weekdays": weekdays, "time_of_day": time_of_day},
        "habits": habit_section,
        "tasks": task_section,
        "checkins": checkins,
    }
    data["insights"] = build_insights(data)
    return data


def build_insights(d):
    """Short, kind, rule-based observations. Each needs enough data to mean something."""
    n, c, h, t, k, p = d["range"], d["consistency"], d["habits"], d["tasks"], d["checkins"], d["patterns"]
    if c["days_tracked"] < 7:
        return [{"kind": "note", "text": "Insights get sharper after about a week of use. Keep checking in and they will fill in."}]
    out = []   # (priority, kind, text); lower priority number shows first

    def add(priority, kind, text):
        out.append((priority, kind, text))

    if c["streak"] >= 3:
        add(1, "good", f"You've been active {c['streak']} days in a row.")
    elif c["streak"] == 0 and c["best_streak"] >= 3:
        add(1, "tip", f"Your best run is {c['best_streak']} days. Today is a good day to start a new one.")

    for hb in [r for r in h["habits"] if r["at_risk"]][:2]:
        add(1, "tip", f"Your {hb['name']} streak ({hb['streak']} days) is waiting for today's check.")

    if c["delta"] is not None:
        if c["delta"] >= 10:
            add(2, "good", f"Consistency is up {c['delta']} points compared with the previous {n} days.")
        elif c["delta"] <= -10:
            add(2, "note", f"Consistency is down {abs(c['delta'])} points from the previous {n} days. Quiet stretches happen, and one small action today restarts the run.")

    if k["past_deadline"] >= 1:
        add(2, "note", f"You went past your {k['deadline_hours']} h deadline {k['past_deadline']} time{'s' if k['past_deadline'] != 1 else ''} in this period, so an alert would have gone out. A quick daily check-in keeps a safe margin.")
    elif k["longest_pct"] is not None and k["longest_pct"] >= 75:
        add(2, "tip", f"Your longest quiet stretch was {_hours_text(k['longest_gap_hours'])}, {k['longest_pct']}% of your deadline. A quick check-in each morning widens the margin.")
    elif k["window_days"] >= 7 and k["pulse_days"] >= 0.8 * k["window_days"]:
        add(3, "good", f"You used the Check-in button on {k['pulse_days']} of {k['window_days']} days.")

    if t["postponed_events"] >= 3:
        add(2, "note", f"You moved tasks to a later day {t['postponed_events']} times ({t['postponed_tasks']} different tasks). Moving things is fine; if the same ones keep sliding, a smaller first step can help.")
    if t["most_postponed"]:
        mp = t["most_postponed"]
        add(2, "tip", f"\"{mp['title']}\" has been pushed {mp['times']} times. Try shrinking it to a 5-minute first step, or let it go.")
    if t["open_overdue"] >= 5:
        add(3, "tip", f"{t['open_overdue']} older tasks are still open. Pick the few that matter and delete or reschedule the rest.")
    if t["planned"] >= 8 and t["rate"] is not None:
        if t["rate"] >= 80:
            add(3, "good", f"You finished {t['done']} of {t['planned']} planned tasks ({t['rate']}%).")
        elif t["rate"] < 50 and t["avg_planned_per_day"] >= 5:
            add(3, "tip", f"You plan about {t['avg_planned_per_day']:g} tasks a day and finish about {t['avg_done_per_day']:g}. Planning fewer, more important tasks may feel better.")
    if t["done"] >= 8 and t["late"] * 2 > t["done"]:
        add(4, "note", "More than half of your finished tasks were done after their planned day.")

    ranked = sorted(((w["rate"], i) for i, w in enumerate(p["weekdays"]) if w["rate"] is not None and w["days"] >= 3))
    if c["window_days"] >= 28 and len(ranked) >= 2 and ranked[-1][0] - ranked[0][0] >= 25:
        hi, lo = ranked[-1], ranked[0]
        add(3, "note", f"You're most consistent on {WEEKDAYS[hi[1]]}s ({hi[0]}% of them active) and quietest on {WEEKDAYS[lo[1]]}s ({lo[0]}%).")

    wd_due = sum(w["due"] for w in h["weekdays"][:5])
    we_due = sum(w["due"] for w in h["weekdays"][5:])
    if wd_due >= 8 and we_due >= 8:
        wd_rate = _pct(sum(w["done"] for w in h["weekdays"][:5]), wd_due)
        we_rate = _pct(sum(w["done"] for w in h["weekdays"][5:]), we_due)
        if abs(wd_rate - we_rate) >= 20:
            lower = "weekends" if we_rate < wd_rate else "weekdays"
            add(3, "note", f"Habit completion is {wd_rate}% on weekdays and {we_rate}% on weekends. If {lower} are harder, you can give each habit its own days.")

    rated = [r for r in h["habits"] if r["rate"] is not None and r["due"] >= 5]
    if rated:
        top, bottom = rated[0], rated[-1]
        if top["rate"] >= 80:
            add(3, "good", f"{top['name']} is your most reliable habit: {top['rate']}% over the last {n} days.")
        if bottom is not top and bottom["rate"] < 50 and bottom["due"] >= 6:
            add(3, "tip", f"{bottom['name']} is at {bottom['rate']}%. A smaller version, or fewer scheduled days, can make it easier to keep.")

    tod = p["time_of_day"]
    top_b = _top_bucket(tod)
    if top_b and tod["total"] >= 15 and top_b["pct"] >= 40:
        add(4, "note", f"Most of what you do happens {BUCKET_PHRASE[top_b['name']]} ({top_b['pct']}% of your actions).")
    task_b = _top_bucket(t["time_of_day"])
    if task_b and t["time_of_day"]["total"] >= 8 and task_b["pct"] >= 45:
        add(4, "note", f"You finish most tasks {BUCKET_PHRASE[task_b['name']]} ({task_b['pct']}%). That's a good slot for the important ones.")

    out.sort(key=lambda item: item[0])
    return [{"kind": kind, "text": text} for _, kind, text in out[:MAX_INSIGHTS]]
