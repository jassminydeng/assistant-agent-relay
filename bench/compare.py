"""Builds the comparison page: every task -> step -> agent, one agent after another, for comparing replies.

It reads runs/*/run.json and data/conversations/<agent>.jsonl as they are and never connects to an agent.
Tasks and steps come from what the runs recorded. Scores, rubrics and takeaways are yours to add, in a notes
file (below). The page shows whatever was said in the chats, so keep it to yourself: it's written to data/,
which is never committed.

Messages are placed in this order: the ids a run recorded, then the run of the message they reply to, then
`filed` in the notes (read by hand), then timing (up to 20 minutes after a run stopped). Anything left is
unprompted.

The notes are tasks/compare-notes.yaml, which is gitignored because rubrics come from the task sheet. All of
it is optional:

    since: 2026-10-27T21:20:00Z        # leave out anything older, like a run before a reset
    timezone: America/New_York         # the computer's own if left out
    agents:                            # which agents to show, in this order, and what to call them
      wajo: Wajo (Fo)
      hark: Hark
    not_started:
      - {name: Task B, why: needs an account set up}
    tasks:                             # in this order; tasks and steps not listed follow, in the order they ran
      - id: a                          # id: what #a in the page's link goes to
        name: Task A                   # the run's --task
        goal: One sentence on what it tests.
        rubric: How you score this task.
        open: Anything still unsettled.
        takeaways: {wajo: "What you noticed.", hark: "What you noticed."}
        steps:
          - id: a1
            title: "First checkpoint"
            runs: [DAY 1 CHECKPOINT]   # the runs' --step, if not the same as title
            scores: {wajo: 8, hark: open}
            note: Anything to know when reading this step.
    filed:                             # messages no run recorded and no reply link places
      - {agent: wajo, id: ":69", step: a1, tag: unprompted}  # id: the end of the message's id
"""

import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from bench.config import NAME, ROOT, ConfigError, load_agents
from bench.conversation import OUT_DIR as CONVERSATIONS

OUT = ROOT / "data" / "compare.html"  # data/ is never committed
NOTES = ROOT / "tasks" / "compare-notes.yaml"  # tasks/ is never committed
RUNS_DIR = ROOT / "runs"
TEMPLATE = Path(__file__).with_name("compare.html")
LATE_WINDOW = timedelta(minutes=20)
COLORS = 6  # --c0 to --c5 in compare.html
NO_TASK = "No task"
NO_STEP = "No step"
ALIASES = ("fo", "the dot")  # what the agents get called in prompts, besides their names


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def duration(a: str, b: str) -> str:
    s = (parse(b) - parse(a)).total_seconds()
    if s < 90:
        return f"{max(int(s), 1)} s"
    return f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def link(m: re.Match) -> str:
    url = html.unescape(m.group(1)).rstrip(".,;:)")
    tail = m.group(1)[len(html.escape(url, quote=False)):] if m.group(1).endswith((".", ",", ";", ":", ")")) else ""
    p = urlparse(url)
    path = p.path if len(p.path) <= 28 else p.path[:27] + "…"
    return f'<a href="{html.escape(url)}" title="{html.escape(url)}">{html.escape(p.netloc + path)}</a>{tail}'


def inline(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{html.escape(html.unescape(m.group(2)))}">{m.group(1)}</a>', t)
    t = re.sub(r'(?<!href=")(?<!">)(https?://[^\s<)\]]+)', link, t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<strong>\1</strong>", t)


def render(text: str) -> str:
    """Just enough Markdown for what the agents send: headings, tables, lists, emphasis, links."""
    out, lines, i = [], (text or "").split("\n"), 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            head, i, rows = cells(s), i + 2, []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i])); i += 1
            out.append("<div class='tw'><table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table></div>")
            continue
        h = re.match(r"^#{1,6}\s+(.*)", s)
        if h:
            out.append(f"<p class='mh'>{inline(h.group(1))}</p>")
        elif re.match(r"^([-•]|\d+[.)])\s+", s):
            items = []
            while i < len(lines) and re.match(r"^\s*([-•]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-•]|\d+[.)])\s+", "", lines[i])); i += 1
            tag = "ol" if re.match(r"^\d", s) else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        elif s:
            out.append(f"<p>{inline(s)}</p>")
        i += 1
    return "".join(out)


def file_url(path: str) -> str:
    return "file://" + quote(path)


def attachments(m: dict, url=file_url) -> str:
    """`url` turns a saved file's path into a link: a file:// one for the written page, the console's own when it serves it."""
    out = []
    for a in m.get("attachments") or []:
        href = html.escape(url(a.get("path") or ""))
        name = html.escape(a.get("name") or "attachment")
        if (a.get("mime_type") or "").startswith("image/"):
            out.append(f'<a href="{href}"><img class="att" src="{href}" alt="{name}" loading="lazy"></a>')
        else:
            out.append(f'<a class="file" href="{href}">{name}</a>')
    return "".join(out)


def checked_id(value, where: str) -> str:
    """Ids end up in the page's attributes and links, so the same rule as agent names applies."""
    value = str(value)
    if not NAME.match(value):
        raise ConfigError(f"{where}: {value!r} must start with a lowercase letter, then lowercase letters, digits, - or _")
    return value


def load_notes(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from None


def moment(value, where: str) -> datetime | None:
    """A time from the notes or the command line. YAML may have already read it as a date. No timezone means local."""
    if value is None:
        return None
    try:
        dt = parse(str(value))
    except ValueError:
        raise ConfigError(f"{where}: {value!r} isn't a date and time, like 2026-10-27T21:20:00Z") from None
    return dt if dt.tzinfo else dt.astimezone()


def timezone(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"timezone: {name!r} isn't a timezone, like America/New_York") from None


def load_messages(agents: dict[str, str], since: datetime | None) -> dict[str, list[dict]]:
    """Each agent's messages. Resets, service notices and anything before `since` are left out."""
    convos = {}
    for a in agents:
        p = CONVERSATIONS / f"{a}.jsonl"
        msgs = [json.loads(line) for line in p.open()] if p.exists() else []
        convos[a] = [m for m in msgs if m.get("role") in ("user", "agent") and not m.get("service")
                     and (not since or parse(m["sent_at"]) >= since)]
    return convos


def load_runs(agents: dict[str, str], since: datetime | None) -> list[dict]:
    runs = [json.loads(p.read_text()) for p in sorted(RUNS_DIR.glob("*/run.json"))]
    return [r for r in runs if r.get("agent") in agents and r.get("started_at") and (not since or parse(r["started_at"]) >= since)]


def build_tasks(notes: dict, runs: list[dict]) -> tuple[list[dict], dict[tuple[str, str], str]]:
    """The tasks in the notes, then whatever else the runs recorded, in the order they ran.
    Also returns (task, step as the run recorded it) -> step id."""
    tasks, step_of = [], {}
    for i, t in enumerate(notes.get("tasks") or [], 1):
        steps = []
        for j, s in enumerate(t.get("steps") or [], 1):
            sid = checked_id(s.get("id") or f"t{i}s{j}", "a step's id in the notes")
            title = str(s.get("title") or s.get("id") or f"Step {j}")
            steps.append({**s, "id": sid, "title": title})
            for raw in s.get("runs", [title]):
                step_of[t["name"], raw] = sid
        tasks.append({**t, "id": checked_id(t.get("id") or f"t{i}", "a task's id in the notes"), "steps": steps})
    by_name = {t["name"]: t for t in tasks}
    for r in sorted(runs, key=lambda r: r["started_at"]):
        name, raw = r.get("task") or NO_TASK, r.get("step") or NO_STEP
        if (name, raw) in step_of:
            continue
        if name not in by_name:
            by_name[name] = {"name": name, "id": f"t{len(tasks) + 1}", "steps": []}
            tasks.append(by_name[name])
        t = by_name[name]
        t["steps"].append({"id": f"{t['id']}s{len(t['steps']) + 1}", "title": raw})
        step_of[name, raw] = t["steps"][-1]["id"]
    return tasks, step_of


def place(convos: dict[str, list[dict]], runs: list[dict], step_of: dict, filed: list[dict]) -> tuple[dict, dict, list[dict]]:
    """Returns step id -> agent -> [(message, tag)], agent -> unprompted messages, and the `filed` entries that matched nothing."""
    run_step = {r["run"]: step_of[r.get("task") or NO_TASK, r.get("step") or NO_STEP] for r in runs}
    by_id = {i: r for r in runs for i in r.get("messages", [])}
    placed, unprompted, used = defaultdict(lambda: defaultdict(list)), defaultdict(list), []
    for a, msgs in convos.items():
        live = sorted((r for r in runs if r["agent"] == a and not str(r.get("finished", "")).startswith("failed")), key=lambda r: r["started_at"])
        for m in msgs:
            t = parse(m["sent_at"])
            run = by_id.get(m["id"]) or by_id.get(m.get("reply_to")) or by_id.get(m.get("thread"))
            entry = next((f for f in filed if f["agent"] == a and m["id"].endswith(str(f["id"]))), None)
            if entry and not by_id.get(m["id"]):
                used.append(entry)
                placed[entry["step"]][a].append((m, entry.get("tag") or ""))
                continue
            tag = ""
            if not run:
                prev = [r for r in live if parse(r["started_at"]) - timedelta(seconds=5) <= t]
                if prev and t <= parse(prev[-1].get("ended_at") or prev[-1]["started_at"]) + LATE_WINDOW:
                    run = prev[-1]
            if not run:
                unprompted[a].append(m)
                continue
            if m["role"] == "agent" and run.get("ended_at") and t > parse(run["ended_at"]):
                tag = "late"
            placed[run_step[run["run"]]][a].append((m, tag))
    return placed, unprompted, [f for f in filed if f not in used]


def build(notes: dict | None = None, since: datetime | None = None, out: Path = OUT, agents: dict[str, str] | None = None,
          attachment_url=file_url) -> tuple[str, list[dict]]:
    """Returns the page, and the `filed` entries in the notes that matched no message."""
    notes = notes or {}
    since = since or moment(notes.get("since"), "since in the notes")
    tz = timezone(notes.get("timezone"))
    if notes.get("agents"):
        agents = {checked_id(a, "an agent in the notes"): str(name) for a, name in notes["agents"].items()}
    elif agents is None:
        agents = {a: a.capitalize() for a in load_agents()}
    short = {a: name.split(" ")[0] for a, name in agents.items()}
    words = {*agents, *ALIASES, *(w.strip("()") for name in agents.values() for w in name.lower().split())}
    agent_word = re.compile(r"\b(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b") if words else None

    def norm(t: str | None) -> str:
        t = (t or "").strip().lower()
        return re.sub(r"\s+", " ", agent_word.sub("[agent]", t) if agent_word else t)

    def local(s: str) -> str:
        return parse(s).astimezone(tz).strftime("%b %-d, %-I:%M %p")

    runs = load_runs(agents, since)
    tasks, step_of = build_tasks(notes, runs)
    ids = {s["id"] for t in tasks for s in t["steps"]}
    filed = notes.get("filed") or []
    for f in filed:
        if f.get("step") not in ids:
            raise ConfigError(f"filed in the notes: no step has the id {f.get('step')!r}")
    placed, unprompted, unmatched = place(load_messages(agents, since), runs, step_of, filed)
    step_runs = defaultdict(list)
    for r in runs:
        step_runs[step_of[r.get("task") or NO_TASK, r.get("step") or NO_STEP]].append(r)
    runs_href = quote(os.path.relpath(RUNS_DIR, out.parent))

    nav, body, board = [], [], []
    for t in tasks:
        nav.append(f"<a class='nt' href='#{t['id']}'>{html.escape(t['name'])}</a>")
        body.append(f"<section class='task' id='{t['id']}'><h2>{html.escape(t['name'])}</h2>")
        if t.get("goal"):
            body.append(f"<p class='goal'>{html.escape(t['goal'])}</p>")
        if t.get("rubric"):
            body.append(f"<p class='rubric'><b>Rubric</b> {html.escape(t['rubric'])}</p>")
        takeaways = t.get("takeaways") or {}
        if takeaways:
            body.append("<div class='tw'><table class='take'><tr><th>Agent</th><th>From your notes</th></tr>"
                        + "".join(f"<tr data-a='{a}'><td class='who'><span class='dot {a}'></span>{html.escape(agents[a])}</td><td>{html.escape(str(takeaways.get(a, '')))}</td></tr>" for a in agents) + "</table></div>")
        if t.get("open"):
            body.append(f"<p class='open'><b>Still open</b> {html.escape(t['open'])}</p>")
        for s in t["steps"]:
            sid, title, scores, note = s["id"], s["title"], s.get("scores"), s.get("note")
            nav.append(f"<a class='ns' href='#{sid}'>{html.escape(title)}</a>")
            scored = [(a, str(scores.get(a, ""))) for a in agents] if scores else []
            if scored:
                board.append(f"<tr><th scope='row'><a href='#{sid}'>{html.escape(t['name'])}<span>{html.escape(title)}</span></a></th>"
                             + "".join(f"<td data-a='{a}' class='{'pend' if v in ('open', 'pending') else ''}'><a href='#{sid}-{a}'>{html.escape(v)}</a></td>" for a, v in scored) + "</tr>")
            rs = sorted(step_runs.get(sid, []), key=lambda r: r["started_at"])
            agents_by_prompt, first_seen = defaultdict(set), {}
            for r in rs:
                pf = RUNS_DIR / r["run"] / "prompt.txt"
                if pf.exists():
                    txt = pf.read_text()
                    agents_by_prompt[norm(txt)].add(r["agent"])
                    first_seen.setdefault(norm(txt), txt)
            shared = [k for k, v in agents_by_prompt.items() if len(v) >= 2]
            body.append(f"<section class='step' id='{sid}'><div class='sh'><h3>{html.escape(title)}</h3>")
            if scored:
                body.append("<div class='sc'>" + "".join(f"<span data-a='{a}'><span class='dot {a}'></span>{html.escape(short[a])} <b>{html.escape(v)}</b></span>" for a, v in scored) + "</div>")
            body.append("</div>")
            if note:
                body.append(f"<p class='note'>{html.escape(note)}</p>")
            for k in shared:
                sent_to = "all agents" if len(agents_by_prompt[k]) == len(agents) else ", ".join(html.escape(short[a]) for a in agents if a in agents_by_prompt[k])
                body.append(f"<div class='prompt'><div class='lab'>Prompt sent to {sent_to}</div>{render(first_seen[k])}</div>")
            body.append("<div class='agents'>")
            for a in agents:
                items = sorted(placed[sid].get(a, []), key=lambda x: x[0]["sent_at"])
                failed = [r for r in rs if r["agent"] == a and str(r.get("finished", "")).startswith("failed")]
                follow = [m for m, _ in items if m["role"] == "user" and norm(m.get("text")) not in shared]
                replies = [m for m, _ in items if m["role"] == "agent"]
                facts = [f"{len(replies)} {'message' if len(replies) == 1 else 'messages'}", f"{len(follow)} {'follow-up' if len(follow) == 1 else 'follow-ups'} from you"]
                if items and items[0][0]["role"] == "user" and replies:
                    facts.append("first reply in " + duration(items[0][0]["sent_at"], replies[0]["sent_at"]))
                if failed:
                    facts.append(f"{len(failed)} failed {'send' if len(failed) == 1 else 'sends'} hidden")
                body.append(f"<article class='agent' id='{sid}-{a}' data-a='{a}'><header><span class='dot {a}'></span><h4>{html.escape(agents[a])}</h4><span class='facts'>{' · '.join(facts)}</span></header>")
                if not items:
                    body.append("<p class='none'>Nothing recorded for this step.</p>")
                for m, tag in items:
                    if m["role"] == "user" and norm(m.get("text")) in shared:
                        body.append(f"<div class='sent'>Prompt sent {local(m['sent_at'])}</div>")
                        continue
                    who = "You" if m["role"] == "user" else html.escape(short[a])
                    label = {"late": "<span class='tag late'>after recording stopped</span>", "unprompted": "<span class='tag unp'>unprompted</span>"}.get(tag, "")
                    text = render(m.get("text")) or ("" if m.get("attachments") else "<p class='none'>No text.</p>")
                    body.append(f"<div class='m m-{m['role']}'><div class='meta'>{who} · {local(m['sent_at'])}{label}</div>{text}{attachments(m, attachment_url)}</div>")
                saved = {}
                for r in rs:
                    if r["agent"] != a:
                        continue
                    for d in r.get("deliverables", []):
                        if d.get("path", "").endswith(".png"):
                            saved.setdefault(d.get("source"), (r["run"], d))
                if saved:
                    links = "".join(f"<li><a href='{runs_href}/{quote(run)}/{quote(d['path'])}'>{html.escape(urlparse(src or '').netloc or 'page')}</a>{' <span class=bad>blocked, ' + str(d.get('status')) + '</span>' if (d.get('status') or 200) >= 400 else ''}</li>" for src, (run, d) in saved.items())
                    body.append(f"<details class='saved'><summary>{len(saved)} saved {'page' if len(saved) == 1 else 'pages'}</summary><ul>{links}</ul></details>")
                body.append("</article>")
            body.append("</div></section>")
        body.append("</section>")

    nav.append("<a class='nt' href='#unprompted'>Everything else, unprompted</a>")
    body.append("<section class='task' id='unprompted'><h2>Everything else, unprompted</h2><p class='goal'>Messages each agent sent on its own that don't belong to a step: reminders, nudges and follow-ups.</p><div class='agents'>")
    for a in agents:
        ms = sorted(unprompted[a], key=lambda m: m["sent_at"])
        body.append(f"<article class='agent' data-a='{a}'><header><span class='dot {a}'></span><h4>{html.escape(agents[a])}</h4><span class='facts'>{len(ms)} messages</span></header>")
        for m in ms:
            who = "You" if m["role"] == "user" else html.escape(short[a])
            body.append(f"<div class='m m-{m['role']}'><div class='meta'>{who} · {local(m['sent_at'])}</div>{render(m.get('text')) or '<p class=none>No text.</p>'}{attachments(m, attachment_url)}</div>")
        body.append("</article>")
    body.append("</div></section>")

    n_step = sum(len(v) for s in placed.values() for v in s.values())
    n_unp = sum(len(v) for v in unprompted.values())
    zone = notes["timezone"].split("/")[-1].replace("_", " ") if tz else "local"
    lede = (f"All messages{since.astimezone(tz).strftime(' since %b %-d') if since else ''}, in step order. Each step shows the shared prompt once, "
            f"then each agent's replies and anything you sent it afterward. Times are {zone}.")
    head = "".join(f"<th data-a='{a}'>{html.escape(agents[a])}</th>" for a in agents)
    board_html = f"<div class='tw'><table class='board'><tr><th>Step</th>{head}</tr>{chr(10).join(board)}</table></div>" if board else ""
    if board:
        lede += " Scores are from your notes; click one to jump to that reply."
    not_started = " · ".join(f"<b>{html.escape(n['name'])}</b>" + (f" ({html.escape(n['why'])})" if n.get("why") else "") for n in notes.get("not_started") or [])
    when = datetime.now(tz).astimezone(tz).strftime("%b %-d, %Y, %-I:%M %p")
    foot = (f"Not started: {not_started}. " if not_started else "") + f"{n_step} messages in steps, {n_unp} unprompted; generated {when}."
    dots = "".join(f".dot.{a}{{background:var(--c{i % COLORS})}}" for i, a in enumerate(agents))
    chips = "".join(f"<button data-f='{a}'><span class='dot {a}'></span>{html.escape(agents[a])}</button>" for a in agents)
    page = (TEMPLATE.read_text()
            .replace("%DOTS%", dots)
            .replace("%COLS%", str(max(len(agents), 1)))
            .replace("%CHIPS%", chips)
            .replace("%LEDE%", lede)
            .replace("%FOOT%", foot)
            .replace("%NAV%", "\n".join(nav))
            .replace("%BOARD%", board_html)
            .replace("%BODY%", "\n".join(body)))  # last: messages could contain any of these
    return page, unmatched


def write(out: Path = OUT, notes_path: Path = NOTES, since: datetime | None = None) -> tuple[Path, list[dict]]:
    """Writes the page. Returns where, and the `filed` entries in the notes that matched no message."""
    page, unmatched = build(load_notes(notes_path), since, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out, unmatched
