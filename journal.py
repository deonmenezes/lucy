"""Generate the journal dashboard from Lucy's memory.

Only the caller keys listed in JOURNAL_KEYS are published. Everyone else who
calls the number stays private, which matters because the number is public and
other callers never agreed to be published.

    uv run python journal.py                 # write site/journal/index.html
    JOURNAL_KEYS="+1555...,local-test" uv run python journal.py

Phone numbers are masked to the last four digits before they reach the page.
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import memory

OUT = Path(__file__).parent / "site" / "journal" / "index.html"
KEYS = [k.strip() for k in os.environ.get("JOURNAL_KEYS", "local-test").split(",") if k.strip()]

# Integrations Lucy does not have yet. Shown so the roadmap is legible, and
# labelled honestly rather than implied to work.
CONNECTORS = [
    ("Phone line", "\U0001F4DE", "connected", "+1 (484) 990-1621, answering right now"),
    ("Local memory", "\U0001F5C4", "connected", "SQLite on this machine, no cloud copy"),
    ("Calendar", "\U0001F4C5", "off", "So she can see what your week looks like"),
    ("Reminders", "⏰", "off", "Push her to-do list to your phone"),
    ("Email", "✉️", "off", "Let her send the follow-up she promised"),
    ("Messages", "\U0001F4AC", "off", "Text you the list after a call"),
]


def mask(key: str) -> str:
    digits = "".join(c for c in key if c.isdigit())
    return f"line ending {digits[-4:]}" if len(digits) >= 4 else key


def local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone()
    except Exception:
        return None


def when(iso: str | None, fmt: str = "%b %-d, %Y") -> str:
    d = local(iso)
    return d.strftime(fmt) if d else ""


def ago(iso: str | None) -> str:
    d = local(iso)
    if not d:
        return ""
    delta = datetime.now(timezone.utc) - d.astimezone(timezone.utc)
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def e(s) -> str:
    return html.escape(str(s or ""))


def build() -> str:
    c = memory.connect()
    q = ",".join("?" * len(KEYS))
    today = datetime.now().astimezone().date()

    people = c.execute(f"SELECT * FROM callers WHERE phone IN ({q})", KEYS).fetchall()
    name = next((p["name"] for p in people if p["name"]), None) or "you"
    first_seen = min((p["first_seen"] for p in people if p["first_seen"]), default=None)

    calls = c.execute(
        f"SELECT * FROM calls WHERE phone IN ({q}) ORDER BY started_at DESC", KEYS
    ).fetchall()
    facts = c.execute(
        f"SELECT fact, created_at FROM facts WHERE phone IN ({q}) ORDER BY id DESC", KEYS
    ).fetchall()
    tasks = c.execute(
        f"SELECT task, done, created_at, done_at FROM tasks WHERE phone IN ({q}) "
        "ORDER BY done, id DESC", KEYS
    ).fetchall()
    turn_total = c.execute(f"SELECT COUNT(*) FROM turns WHERE phone IN ({q})", KEYS).fetchone()[0]

    def is_today(iso):
        d = local(iso)
        return bool(d and d.date() == today)

    calls_today = [r for r in calls if is_today(r["started_at"])]
    facts_today = [r for r in facts if is_today(r["created_at"])]
    tasks_added_today = [r for r in tasks if is_today(r["created_at"])]
    tasks_done_today = [r for r in tasks if r["done"] and is_today(r["done_at"])]
    open_tasks = [r for r in tasks if not r["done"]]

    # ---------- what happened today ----------
    if calls_today or facts_today or tasks_added_today:
        bits = []
        if calls_today:
            bits.append(f"{len(calls_today)} call{'s' if len(calls_today) != 1 else ''}")
        if facts_today:
            bits.append(f"{len(facts_today)} new thing{'s' if len(facts_today) != 1 else ''} learned")
        if tasks_added_today:
            bits.append(f"{len(tasks_added_today)} added to your list")
        if tasks_done_today:
            bits.append(f"{len(tasks_done_today)} checked off")
        today_line = ", ".join(bits) + "."
        events = []
        for r in calls_today:
            events.append(
                f'<li class="ev"><span class="ev-time">{e(when(r["started_at"], "%-I:%M %p"))}</span>'
                f'<span class="ev-dot call"></span>'
                f'<span class="ev-text">You called. {e(r["summary"] or "Conversation recorded.")}</span></li>'
            )
        for r in facts_today:
            events.append(
                f'<li class="ev"><span class="ev-time">{e(when(r["created_at"], "%-I:%M %p"))}</span>'
                f'<span class="ev-dot fact"></span>'
                f'<span class="ev-text">Lucy noted: {e(r["fact"])}</span></li>'
            )
        for r in tasks_done_today:
            events.append(
                f'<li class="ev"><span class="ev-time">{e(when(r["done_at"], "%-I:%M %p"))}</span>'
                f'<span class="ev-dot done"></span>'
                f'<span class="ev-text">Checked off: {e(r["task"])}</span></li>'
            )
        today_events = "\n".join(events) or '<li class="ev empty">Nothing logged yet today.</li>'
    else:
        today_line = "Nothing yet today. Call her and this fills in on its own."
        today_events = '<li class="ev empty">No activity today.</li>'

    # ---------- calls ----------
    call_rows = []
    for r in calls:
        turns = c.execute(
            "SELECT COUNT(*) FROM turns WHERE call_id=?", (r["call_id"],)
        ).fetchone()[0]
        if not turns:
            continue
        call_rows.append(f"""
        <li class="row">
          <div class="row-main">
            <span class="row-title">{e(when(r["started_at"], "%A, %B %-d"))}</span>
            <span class="row-sub">{e(r["summary"] or "Conversation recorded.")}</span>
          </div>
          <div class="row-meta">
            <span class="pill">{turns} exchanges</span>
            <span class="row-when">{e(ago(r["started_at"]))}</span>
          </div>
        </li>""")
    calls_html = "\n".join(call_rows) or '<li class="row empty">No calls yet.</li>'

    # ---------- diary ----------
    diary = []
    for r in calls:
        turns = c.execute(
            "SELECT speaker, text FROM turns WHERE call_id=? ORDER BY id", (r["call_id"],)
        ).fetchall()
        if not turns:
            continue
        lines = "\n".join(
            f'<div class="t {"her" if t["speaker"] == "agent" else "you"}">'
            f'<span class="t-who">{"Lucy" if t["speaker"] == "agent" else e(name)}</span>'
            f'<p class="t-said">{e(t["text"])}</p></div>'
            for t in turns
        )
        diary.append(f"""
        <article class="day">
          <header class="day-head">
            <h3 class="day-date">{e(when(r["started_at"], "%A, %B %-d"))}</h3>
            <span class="day-time">{e(when(r["started_at"], "%-I:%M %p"))}</span>
          </header>
          <p class="day-summary">{e(r["summary"] or "Conversation recorded.")}</p>
          <details class="day-full">
            <summary>Read the full conversation</summary>
            <div class="t-list">{lines}</div>
          </details>
        </article>""")
    diary_html = "\n".join(diary) or '<p class="empty-note">Your diary starts with your first call.</p>'

    # ---------- knows / list / connectors ----------
    knows_html = "\n".join(
        f'<li class="chip"><span class="chip-text">{e(f["fact"])}</span>'
        f'<span class="chip-when">{e(ago(f["created_at"]))}</span></li>'
        for f in facts
    ) or '<li class="chip empty"><span class="chip-text">Nothing yet.</span></li>'

    list_html = "\n".join(
        f'<li class="task {"is-done" if t["done"] else ""}">'
        f'<span class="box">{"&#10003;" if t["done"] else ""}</span>'
        f'<span class="task-text">{e(t["task"])}</span></li>'
        for t in tasks
    ) or '<li class="task empty"><span class="box"></span>'\
         '<span class="task-text">Nothing on your list.</span></li>'

    conn_html = "\n".join(
        f'<li class="conn"><span class="conn-icon">{icon}</span>'
        f'<span class="conn-body"><b>{e(label)}</b><span>{e(note)}</span></span>'
        f'<span class="status {state}">{"Connected" if state == "connected" else "Not connected"}</span></li>'
        for label, icon, state, note in CONNECTORS
    )

    out = TEMPLATE
    for token, value in {
        "%%NAME%%": e(name),
        "%%CALLS%%": str(len(calls)),
        "%%TURNS%%": str(turn_total),
        "%%FACTS%%": str(len(facts)),
        "%%OPEN%%": str(len(open_tasks)),
        "%%SINCE%%": e(when(first_seen)) or "today",
        "%%TODAY_DATE%%": e(datetime.now().astimezone().strftime("%A, %B %-d")),
        "%%TODAY_LINE%%": e(today_line),
        "%%TODAY_EVENTS%%": today_events,
        "%%CALL_ROWS%%": calls_html,
        "%%DIARY%%": diary_html,
        "%%KNOWS%%": knows_html,
        "%%LIST%%": list_html,
        "%%CONNECTORS%%": conn_html,
        "%%STAMP%%": e(datetime.now().astimezone().strftime("%b %-d, %Y at %-I:%M %p")),
        "%%IDENTITIES%%": e(", ".join(mask(k) for k in KEYS)),
    }.items():
        out = out.replace(token, value)
    return out


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your Journal</title>
<meta name="description" content="Everything Lucy remembers, written down after every phone call.">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#ffffff">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>%F0%9F%93%94</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    color-scheme: light;
    --bg: #ffffff;
    --panel: #ffffff;
    --sunken: #f7f8f8;
    --line: #e6e8e8;
    --line-soft: #f0f1f1;
    --text: #16181a;
    --text-2: #5c6469;
    --text-3: #8b9296;
    --accent: #d8380f;
    --accent-soft: #fdf0ec;
    --green: #12805c;
    --green-soft: #e8f5f0;
    --radius: 10px;
    --f: "Instrument Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    --m: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--f); font-size: 15px; line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; }
  :is(a, summary, button):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; }

  /* top bar */
  .bar {
    position: sticky; top: 0; z-index: 10;
    background: var(--bg); border-bottom: 1px solid var(--line);
  }
  .bar-in {
    max-width: 1080px; margin: 0 auto; padding: 0 20px;
    height: 56px; display: flex; align-items: center; gap: 16px;
  }
  .logo { display: flex; align-items: center; gap: 9px; font-weight: 600; text-decoration: none; }
  .logo i { font-style: normal; width: 26px; height: 26px; border-radius: 7px;
    background: var(--accent); color: #fff; display: grid; place-items: center; font-size: 13px; }
  .bar-nav { margin-left: auto; display: flex; gap: 4px; }
  .bar-nav a {
    text-decoration: none; color: var(--text-2); font-size: 13.5px; font-weight: 500;
    padding: 7px 11px; border-radius: 7px;
  }
  .bar-nav a:hover { background: var(--sunken); color: var(--text); }
  @media (max-width: 700px) { .bar-nav a.opt { display: none; } }

  .page { max-width: 1080px; margin: 0 auto; padding: 26px 20px 64px; }

  .head { margin-bottom: 22px; }
  .head h1 { font-size: 26px; }
  .head p { margin: 5px 0 0; color: var(--text-2); }

  /* stat tiles */
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .tile { border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; background: var(--panel); }
  .tile b { display: block; font-size: 26px; font-weight: 600; line-height: 1.15;
    font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
  .tile span { font-size: 12.5px; color: var(--text-3); }

  /* cards */
  .card {
    border: 1px solid var(--line); border-radius: var(--radius);
    background: var(--panel); margin-top: 22px; overflow: hidden;
  }
  .card-head {
    display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
    padding: 14px 18px; border-bottom: 1px solid var(--line-soft);
  }
  .card-head h2 { font-size: 15.5px; }
  .card-head .note { font-size: 12.5px; color: var(--text-3); }
  .card-body { padding: 6px 18px 16px; }
  .card-body.flush { padding: 0; }

  .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; }
  .grid-2 .card { margin-top: 0; }

  /* today feed */
  .feed { list-style: none; margin: 0; padding: 0; }
  .ev { display: grid; grid-template-columns: 74px 10px 1fr; align-items: start; gap: 10px;
    padding: 11px 18px; border-bottom: 1px solid var(--line-soft); }
  .ev:last-child { border-bottom: 0; }
  .ev-time { font-family: var(--m); font-size: 12px; color: var(--text-3); padding-top: 2px; }
  .ev-dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 7px; background: var(--text-3); }
  .ev-dot.call { background: var(--accent); }
  .ev-dot.fact { background: #2f6fd0; }
  .ev-dot.done { background: var(--green); }
  .ev-text { font-size: 14.5px; }
  .ev.empty { grid-template-columns: 1fr; color: var(--text-3); }

  /* rows */
  .rows { list-style: none; margin: 0; padding: 0; }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 14px;
    padding: 13px 18px; border-bottom: 1px solid var(--line-soft); }
  .row:last-child { border-bottom: 0; }
  .row-main { display: flex; flex-direction: column; min-width: 0; }
  .row-title { font-weight: 600; font-size: 14.5px; }
  .row-sub { color: var(--text-2); font-size: 13.5px; overflow: hidden;
    display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; }
  .row-meta { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .row-when { font-family: var(--m); font-size: 11.5px; color: var(--text-3); }
  .row.empty { color: var(--text-3); }
  .pill { font-size: 11.5px; color: var(--text-2); background: var(--sunken);
    border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; white-space: nowrap; }

  /* knows chips */
  .chips { list-style: none; margin: 0; padding: 10px 0 0; display: grid; gap: 8px; }
  .chip { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
    background: var(--sunken); border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; }
  .chip-text { font-size: 14px; }
  .chip-when { font-family: var(--m); font-size: 11px; color: var(--text-3); white-space: nowrap; }
  .chip.empty { color: var(--text-3); }

  /* task list */
  .tasks { list-style: none; margin: 0; padding: 10px 0 0; display: grid; gap: 2px; }
  .task { display: grid; grid-template-columns: 20px 1fr; gap: 10px; align-items: start;
    padding: 8px 2px; font-size: 14.5px; }
  .box { width: 17px; height: 17px; border: 1.5px solid var(--line); border-radius: 5px;
    display: grid; place-items: center; font-size: 11px; color: #fff; margin-top: 2px; }
  .task.is-done .box { background: var(--green); border-color: var(--green); }
  .task.is-done .task-text { color: var(--text-3); text-decoration: line-through; }
  .task.empty .task-text { color: var(--text-3); }

  /* diary */
  .day { padding: 16px 18px; border-bottom: 1px solid var(--line-soft); }
  .day:last-child { border-bottom: 0; }
  .day-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
  .day-date { font-size: 15px; }
  .day-time { font-family: var(--m); font-size: 11.5px; color: var(--text-3); }
  .day-summary { margin: 7px 0 0; color: var(--text-2); }
  .day-full { margin-top: 10px; }
  .day-full summary { cursor: pointer; font-size: 13px; font-weight: 500; color: var(--accent); }
  .t-list { margin-top: 12px; border: 1px solid var(--line); border-radius: 8px;
    background: var(--sunken); padding: 14px; display: flex; flex-direction: column; gap: 11px; }
  .t { display: grid; grid-template-columns: 52px 1fr; gap: 10px; }
  .t-who { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;
    color: var(--text-3); padding-top: 2px; }
  .t.her .t-who { color: var(--accent); }
  .t-said { margin: 0; font-size: 14px; }
  .empty-note { color: var(--text-3); padding: 14px 18px; margin: 0; }

  /* connectors */
  .conns { list-style: none; margin: 0; padding: 0; }
  .conn { display: flex; align-items: center; gap: 12px; padding: 12px 18px;
    border-bottom: 1px solid var(--line-soft); }
  .conn:last-child { border-bottom: 0; }
  .conn-icon { width: 32px; height: 32px; border-radius: 8px; background: var(--sunken);
    border: 1px solid var(--line); display: grid; place-items: center; font-size: 15px; flex-shrink: 0; }
  .conn-body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
  .conn-body b { font-size: 14.5px; font-weight: 600; }
  .conn-body span { font-size: 13px; color: var(--text-2); }
  .status { font-size: 11.5px; font-weight: 500; border-radius: 999px; padding: 3px 10px;
    white-space: nowrap; flex-shrink: 0; }
  .status.connected { color: var(--green); background: var(--green-soft); }
  .status.off { color: var(--text-3); background: var(--sunken); border: 1px solid var(--line); }

  footer { max-width: 1080px; margin: 0 auto; padding: 20px; border-top: 1px solid var(--line);
    display: flex; flex-wrap: wrap; gap: 8px 20px; justify-content: space-between;
    font-size: 12.5px; color: var(--text-3); }
</style>
</head>
<body>

<div class="bar"><div class="bar-in">
  <a class="logo" href="/"><i>&#9742;</i> Her</a>
  <nav class="bar-nav">
    <a href="#today">Today</a>
    <a href="#calls">Your calls</a>
    <a href="#diary">Your diary</a>
    <a class="opt" href="#connectors">Connectors</a>
    <a class="opt" href="/">Back to site</a>
  </nav>
</div></div>

<main class="page">

  <div class="head">
    <h1>Your journal</h1>
    <p>Everything Lucy wrote down for %%NAME%%. This page fills itself in after every call.</p>
  </div>

  <div class="tiles">
    <div class="tile"><b>%%CALLS%%</b><span>Calls</span></div>
    <div class="tile"><b>%%TURNS%%</b><span>Things said</span></div>
    <div class="tile"><b>%%FACTS%%</b><span>Facts kept</span></div>
    <div class="tile"><b>%%OPEN%%</b><span>Open to-dos</span></div>
    <div class="tile"><b>%%SINCE%%</b><span>Since</span></div>
  </div>

  <section class="card" id="today">
    <div class="card-head">
      <h2>What happened today</h2>
      <span class="note">%%TODAY_DATE%%</span>
    </div>
    <div class="card-body flush">
      <p style="padding:12px 18px 4px;margin:0;color:var(--text-2)">%%TODAY_LINE%%</p>
      <ul class="feed">%%TODAY_EVENTS%%</ul>
    </div>
  </section>

  <section class="card" id="calls">
    <div class="card-head">
      <h2>Your calls</h2>
      <span class="note">Newest first</span>
    </div>
    <div class="card-body flush"><ul class="rows">%%CALL_ROWS%%</ul></div>
  </section>

  <div class="grid-2" style="margin-top:22px">
    <section class="card" id="knows">
      <div class="card-head"><h2>What she knows about you</h2></div>
      <div class="card-body"><ul class="chips">%%KNOWS%%</ul></div>
    </section>

    <section class="card" id="list">
      <div class="card-head"><h2>Your list</h2><span class="note">Ask her on a call</span></div>
      <div class="card-body"><ul class="tasks">%%LIST%%</ul></div>
    </section>
  </div>

  <section class="card" id="diary">
    <div class="card-head">
      <h2>Your diary</h2>
      <span class="note">Every conversation, in full</span>
    </div>
    <div class="card-body flush">%%DIARY%%</div>
  </section>

  <section class="card" id="connectors">
    <div class="card-head">
      <h2>Connectors</h2>
      <span class="note">What she can reach</span>
    </div>
    <div class="card-body flush"><ul class="conns">%%CONNECTORS%%</ul></div>
  </section>

</main>

<footer>
  <span>Updated %%STAMP%%</span>
  <span>Published from %%IDENTITIES%%</span>
  <span><a href="/">her-ai-friend.vercel.app</a></span>
</footer>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) for keys: {KEYS}")
