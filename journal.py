"""Generate the public journal page from Lucy's memory.

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
from datetime import datetime
from pathlib import Path

import memory

OUT = Path(__file__).parent / "site" / "journal" / "index.html"
KEYS = [k.strip() for k in os.environ.get("JOURNAL_KEYS", "local-test").split(",") if k.strip()]


def mask(key: str) -> str:
    digits = "".join(c for c in key if c.isdigit())
    if len(digits) >= 4:
        return f"the line ending {digits[-4:]}"
    return key


def when(iso: str | None, fmt: str = "%B %-d, %Y at %-I:%M %p") -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime(fmt)
    except Exception:
        return iso


def e(s) -> str:
    return html.escape(str(s or ""))


def build() -> str:
    c = memory.connect()
    q = ",".join("?" * len(KEYS))

    people = c.execute(f"SELECT * FROM callers WHERE phone IN ({q})", KEYS).fetchall()
    name = next((p["name"] for p in people if p["name"]), None) or "You"
    total_calls = sum(p["call_count"] or 0 for p in people)
    first_seen = min((p["first_seen"] for p in people if p["first_seen"]), default=None)

    facts = c.execute(
        f"SELECT fact, created_at FROM facts WHERE phone IN ({q}) ORDER BY id DESC", KEYS
    ).fetchall()

    calls = c.execute(
        f"SELECT * FROM calls WHERE phone IN ({q}) ORDER BY started_at DESC", KEYS
    ).fetchall()

    turn_total = c.execute(
        f"SELECT COUNT(*) FROM turns WHERE phone IN ({q})", KEYS
    ).fetchone()[0]

    entries = []
    for call in calls:
        turns = c.execute(
            "SELECT speaker, text FROM turns WHERE call_id=? ORDER BY id", (call["call_id"],)
        ).fetchall()
        if not turns:
            continue
        rows = "\n".join(
            f'<div class="turn {"lucy" if t["speaker"] == "agent" else ""}">'
            f'<span class="who">{"Lucy" if t["speaker"] == "agent" else name}</span>'
            f'<p class="said">{e(t["text"])}</p></div>'
            for t in turns
        )
        summary = (
            f'<p class="entry-summary">{e(call["summary"])}</p>' if call["summary"] else ""
        )
        entries.append(f"""
        <article class="entry">
          <header class="entry-head">
            <h2 class="entry-date">{e(when(call["started_at"], "%B %-d, %Y"))}</h2>
            <span class="entry-time">{e(when(call["started_at"], "%-I:%M %p"))} · {len(turns)} exchanges</span>
          </header>
          {summary}
          <details class="entry-full">
            <summary>Read the whole conversation</summary>
            <div class="entry-turns">{rows}</div>
          </details>
        </article>""")

    fact_items = "\n".join(f"<li>{e(f['fact'])}</li>" for f in facts) or \
        "<li class='empty'>Nothing yet. Call her and she will start filling this in.</li>"

    entries_html = "\n".join(entries) or """
        <article class="entry">
          <p class="entry-summary">No conversations recorded yet. The journal writes
          itself as soon as you hang up.</p>
        </article>"""

    stamp = datetime.now().astimezone().strftime("%B %-d, %Y at %-I:%M %p %Z")
    identities = ", ".join(mask(k) for k in KEYS)

    return TEMPLATE.format(
        name=e(name),
        total_calls=total_calls,
        turn_total=turn_total,
        fact_count=len(facts),
        since=e(when(first_seen, "%B %-d, %Y")) or "today",
        identities=e(identities),
        facts=fact_items,
        entries=entries_html,
        stamp=e(stamp),
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Journal</title>
<meta name="description" content="Everything Lucy remembers, written down after every phone call.">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#F6F3EE" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#14100E" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>%F0%9F%93%94</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&family=Instrument+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {{
    color-scheme: light dark;
    --ink:#14100E; --ink-soft:#3B322D; --paper:#F6F3EE; --paper-2:#EDE7DE;
    --signal:#E8340C; --signal-ink:#fff; --slab:#FFD9CF; --slab-ink:#14100E;
    --grey:#8A7F79; --rule:#14100E; --card:#fff; --shadow:#14100E;
    --f-display:"Anton","Arial Narrow",sans-serif;
    --f-body:"Instrument Sans",ui-sans-serif,system-ui,sans-serif;
    --f-mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ink:#F2EBE4; --ink-soft:#C7BAB0; --paper:#14100E; --paper-2:#1E1815;
      --signal:#FF5A32; --signal-ink:#14100E; --slab:#4A1E12; --slab-ink:#FFD9CF;
      --grey:#9A8C84; --rule:#4A3F39; --card:#1E1815; --shadow:#000;
    }}
  }}
  :root[data-theme="dark"] {{
    --ink:#F2EBE4; --ink-soft:#C7BAB0; --paper:#14100E; --paper-2:#1E1815;
    --signal:#FF5A32; --signal-ink:#14100E; --slab:#4A1E12; --slab-ink:#FFD9CF;
    --grey:#9A8C84; --rule:#4A3F39; --card:#1E1815; --shadow:#000;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
    font-family:var(--f-body); font-size:17px; line-height:1.62; }}
  .wrap {{ max-width:820px; margin:0 auto; padding-inline:clamp(18px,4vw,40px); }}
  a {{ color:inherit; }}
  :is(a,summary):focus-visible {{ outline:3px solid var(--signal); outline-offset:3px; }}

  .top {{ border-bottom:3px solid var(--rule); background:var(--paper);
    position:sticky; top:0; z-index:5; }}
  .top-in {{ display:flex; align-items:center; justify-content:space-between;
    gap:14px; padding-block:13px; }}
  .brand {{ font-family:var(--f-display); font-size:22px; text-transform:uppercase;
    text-decoration:none; display:flex; align-items:center; gap:10px; }}
  .brand span.m {{ width:30px; height:30px; display:grid; place-items:center;
    background:var(--signal); color:var(--signal-ink); border:3px solid var(--rule);
    transform:rotate(-4deg); font-size:15px; }}
  .back {{ font-family:var(--f-mono); font-size:12px; letter-spacing:.13em;
    text-transform:uppercase; text-decoration:none; font-weight:600; }}
  .back:hover {{ color:var(--signal); }}

  header.hero {{ padding-block:clamp(34px,6vw,64px); border-bottom:3px solid var(--rule);
    background-image:radial-gradient(var(--rule) 1.2px,transparent 1.2px);
    background-size:22px 22px; }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) header.hero {{
      background-image:radial-gradient(#3A302B 1.2px,transparent 1.2px); }}
  }}
  :root[data-theme="dark"] header.hero {{
    background-image:radial-gradient(#3A302B 1.2px,transparent 1.2px); }}
  h1 {{ font-family:var(--f-display); text-transform:uppercase; font-weight:400;
    line-height:.92; margin:0; font-size:clamp(40px,9vw,84px); }}
  .kicker {{ font-family:var(--f-mono); font-size:12px; letter-spacing:.18em;
    text-transform:uppercase; color:var(--signal); font-weight:600; margin-bottom:14px; }}
  .sub {{ margin:16px 0 0; max-width:56ch; color:var(--ink-soft); }}

  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:12px; margin-top:26px; }}
  .stat {{ border:3px solid var(--rule); background:var(--card); padding:13px 15px;
    box-shadow:5px 5px 0 var(--shadow); }}
  .stat b {{ display:block; font-family:var(--f-mono); font-size:27px; font-weight:600;
    line-height:1.1; font-variant-numeric:tabular-nums; }}
  .stat span {{ font-family:var(--f-mono); font-size:11px; letter-spacing:.13em;
    text-transform:uppercase; color:var(--grey); }}

  section {{ padding-block:clamp(34px,5vw,58px); }}
  .sec-title {{ font-family:var(--f-display); text-transform:uppercase; font-weight:400;
    font-size:clamp(24px,4vw,38px); margin:0 0 6px; }}
  .sec-note {{ margin:0 0 22px; color:var(--grey); font-size:15px; }}

  .knows {{ list-style:none; margin:0; padding:0; display:grid; gap:9px; }}
  .knows li {{ border:2px solid var(--rule); background:var(--card); padding:12px 14px;
    font-size:15.5px; display:grid; grid-template-columns:auto 1fr; gap:10px; }}
  .knows li::before {{ content:"\\2713"; color:var(--signal); font-weight:700; }}
  .knows li.empty {{ color:var(--grey); }}
  .knows li.empty::before {{ content:"\\00B7"; }}

  .entry {{ border-top:3px solid var(--rule); padding-block:24px; }}
  .entry-head {{ display:flex; align-items:baseline; justify-content:space-between;
    gap:14px; flex-wrap:wrap; }}
  .entry-date {{ font-family:var(--f-display); text-transform:uppercase; font-weight:400;
    font-size:clamp(21px,3vw,29px); margin:0; }}
  .entry-time {{ font-family:var(--f-mono); font-size:12px; letter-spacing:.1em;
    text-transform:uppercase; color:var(--grey); }}
  .entry-summary {{ margin:12px 0 0; font-size:17.5px; }}
  .entry-full {{ margin-top:14px; }}
  .entry-full summary {{ cursor:pointer; font-family:var(--f-mono); font-size:12px;
    letter-spacing:.13em; text-transform:uppercase; color:var(--signal); font-weight:600; }}
  .entry-turns {{ margin-top:14px; border:3px solid var(--rule); background:var(--card);
    padding:16px; display:flex; flex-direction:column; gap:12px;
    font-family:var(--f-mono); font-size:13.5px; line-height:1.55; }}
  .turn {{ display:grid; grid-template-columns:64px 1fr; gap:11px; }}
  .who {{ font-size:10.5px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
    color:var(--grey); padding-top:3px; }}
  .turn.lucy .who {{ color:var(--signal); }}
  .said {{ margin:0; }}

  footer {{ border-top:3px solid var(--rule); padding-block:26px;
    font-family:var(--f-mono); font-size:12px; color:var(--grey);
    display:flex; flex-wrap:wrap; gap:10px 22px; justify-content:space-between; }}
</style>
</head>
<body>

<div class="top"><div class="wrap top-in">
  <a class="brand" href="/"><span class="m">&#9742;</span>Her</a>
  <a class="back" href="/">&larr; Back to the line</a>
</div></div>

<header class="hero"><div class="wrap">
  <div class="kicker">The journal of {name}</div>
  <h1>Everything she<br>wrote down</h1>
  <p class="sub">This page writes itself. Every time you hang up, Lucy works out what
  mattered and adds it here. Nothing was typed by hand.</p>
  <div class="stats">
    <div class="stat"><b>{total_calls}</b><span>Calls</span></div>
    <div class="stat"><b>{turn_total}</b><span>Things said</span></div>
    <div class="stat"><b>{fact_count}</b><span>Facts kept</span></div>
    <div class="stat"><b>{since}</b><span>Since</span></div>
  </div>
</div></header>

<main>
  <section class="wrap">
    <h2 class="sec-title">What she knows about you</h2>
    <p class="sec-note">Pulled out of your conversations automatically, newest first.</p>
    <ul class="knows">{facts}</ul>
  </section>

  <section class="wrap">
    <h2 class="sec-title">Every call</h2>
    <p class="sec-note">Newest first. Open any entry to read the whole thing back.</p>
    {entries}
  </section>
</main>

<footer class="wrap">
  <span>Updated {stamp}</span>
  <span>Published from {identities}</span>
  <span><a href="/">her-ai-friend.vercel.app</a></span>
</footer>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) for keys: {KEYS}")
