import json

import pytest

from bench import cli, compare
from bench.config import ConfigError

AGENTS = {"alpha": "Alpha (Al)", "beta": "Beta"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(compare, "CONVERSATIONS", tmp_path / "conversations")
    (tmp_path / "runs").mkdir()
    (tmp_path / "conversations").mkdir()
    return tmp_path


def add_run(repo, agent, task="Travel", step="DAY 1", prompt="Book a trip", start="2026-10-28T10:00:00Z", end="2026-10-28T10:05:00Z", **fields):
    run = f"{start[:19].replace(':', '')}-{agent}"
    folder = repo / "runs" / run
    folder.mkdir()
    (folder / "prompt.txt").write_text(prompt)
    (folder / "run.json").write_text(json.dumps({"run": run, "task": task, "step": step, "agent": agent, "started_at": start, "ended_at": end, "finished": "quiet for 90s", **fields}))
    return run


def add_messages(repo, agent, *messages):
    with (repo / "conversations" / f"{agent}.jsonl").open("a") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


def msg(id, role="agent", text="Done", at="2026-10-28T10:01:00Z", **fields):
    return {"id": id, "role": role, "text": text, "sent_at": at, **fields}


def build(notes=None, agents=AGENTS, **kw):
    return compare.build(notes, agents=None if notes and notes.get("agents") else agents, **kw)[0]


def test_nothing_recorded_yet_still_makes_a_page(repo):
    page = build()
    assert "0 messages in steps, 0 unprompted" in page
    assert "class='board'" not in page
    assert "%" + "BODY%" not in page


def test_steps_come_from_the_runs_without_any_notes(repo):
    add_run(repo, "alpha")
    add_run(repo, "beta", step="DAY 2", start="2026-10-28T11:00:00Z", end="2026-10-28T11:05:00Z")
    page = build()
    assert "<h2>Travel</h2>" in page
    assert page.index("<h3>DAY 1</h3>") < page.index("<h3>DAY 2</h3>")
    assert "class='rubric'" not in page and "From your notes" not in page


def test_a_prompt_two_agents_got_shows_once_even_with_their_own_names_in_it(repo):
    add_run(repo, "alpha", prompt="Alpha, book a trip")
    add_run(repo, "beta", prompt="Beta, book a trip")
    add_messages(repo, "alpha", msg("a1", "user", "Alpha, book a trip", "2026-10-28T10:00:01Z"))
    page = build()
    assert page.count("class='prompt'") == 1
    assert "Prompt sent to all agents" in page
    assert "Prompt sent Oct 28" in page


def test_messages_go_to_their_run_then_reply_then_filed_then_timing_and_the_rest_are_unprompted(repo):
    run = add_run(repo, "alpha", messages=["recorded"])
    add_messages(repo, "alpha",
                 msg("recorded", text="by id"),
                 msg("answer", text="by reply", at="2026-10-28T20:00:00Z", reply_to="recorded"),
                 msg("x:69", text="by hand", at="2026-10-28T21:00:00Z"),
                 msg("soon", text="by timing", at="2026-10-28T10:15:00Z"),
                 msg("later", text="on its own", at="2026-10-28T22:00:00Z"))
    tasks, step_of = compare.build_tasks({}, compare.load_runs(AGENTS, None))
    filed = [{"agent": "alpha", "id": ":69", "step": "t1s1", "tag": "unprompted"}]
    placed, unprompted, unmatched = compare.place(compare.load_messages(AGENTS, None), compare.load_runs(AGENTS, None), step_of, filed)
    assert [(m["text"], tag) for m, tag in placed["t1s1"]["alpha"]] == [
        ("by id", ""), ("by reply", "late"), ("by hand", "unprompted"), ("by timing", "late")]
    assert [m["text"] for m in unprompted["alpha"]] == ["on its own"]
    assert unmatched == [] and run


def test_notes_add_scores_rubric_and_order_and_runs_they_miss_still_show(repo):
    add_run(repo, "alpha", task="Unlisted", step="DAY 9")
    add_run(repo, "alpha", step="RAW STEP", start="2026-10-28T11:00:00Z", end="2026-10-28T11:05:00Z")
    notes = {"agents": {"beta": "Beta", "alpha": "Alpha (Al)"}, "not_started": [{"name": "Finance", "why": "needs an account"}],
             "tasks": [{"id": "tr", "name": "Travel", "goal": "Goal text", "rubric": "10 = perfect", "open": "Still going",
                        "takeaways": {"alpha": "Fast"},
                        "steps": [{"id": "tr1", "title": "First leg", "runs": ["RAW STEP"], "scores": {"alpha": 10, "beta": "open"}, "note": "Note text"}]}]}
    page = build(notes)
    for text in ("Goal text", "10 = perfect", "Still going", "Fast", "Note text", "<b>Finance</b> (needs an account)", "class='board'", "<a href='#tr1-alpha'>10</a>", "class='pend'"):
        assert text in page
    assert page.index("<h2>Travel</h2>") < page.index("<h2>Unlisted</h2>")
    assert page.index("<button data-f='beta'>") < page.index("<button data-f='alpha'>")
    assert "RAW STEP" not in page


def test_since_leaves_out_older_runs_and_messages(repo):
    add_run(repo, "alpha", step="OLD", start="2026-10-20T10:00:00Z", end="2026-10-20T10:05:00Z")
    add_run(repo, "alpha", step="NEW")
    add_messages(repo, "alpha", msg("old", text="old reply", at="2026-10-20T10:01:00Z"), msg("new", text="new reply"))
    page = build(since=compare.moment("2026-10-27T21:20:00Z", "--since"))
    assert "new reply" in page and "NEW" in page
    assert "old reply" not in page and "OLD" not in page


def test_message_text_is_escaped_and_cant_fill_in_the_template(repo):
    add_run(repo, "alpha")
    add_messages(repo, "alpha", msg("a", text="<script>alert(1)</script> %FOOT%"))
    page = build()
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; %FOOT%" in page


def test_saved_pages_link_relative_to_where_the_page_is_written(repo):
    run = add_run(repo, "alpha", deliverables=[{"path": "deliverables/a.png", "source": "https://example.com/x", "status": 200}])
    page = build(out=repo / "somewhere" / "compare.html")
    assert f"href='../runs/{run}/deliverables/a.png'" in page


def test_each_agent_gets_a_color_however_many_there_are(repo):
    agents = {f"a{i}": f"A{i}" for i in range(8)}
    page = build(agents=agents)
    assert ".dot.a0{background:var(--c0)}" in page and ".dot.a7{background:var(--c1)}" in page


def test_a_filed_entry_for_a_step_that_doesnt_exist_is_refused(repo):
    with pytest.raises(ConfigError, match="nope"):
        build({"filed": [{"agent": "alpha", "id": ":1", "step": "nope"}]})


def test_an_id_that_could_break_the_page_is_refused(repo):
    with pytest.raises(ConfigError):
        build({"tasks": [{"id": "x' onclick='y", "name": "T"}]})


def test_notes_with_a_bad_time_or_timezone_are_refused(repo):
    with pytest.raises(ConfigError, match="since"):
        build({"since": "soon"})
    with pytest.raises(ConfigError, match="timezone"):
        build({"timezone": "Mars/Olympus"})


def test_the_command_reads_the_notes_and_writes_the_page(repo, capsys):
    (repo / "notes.yaml").write_text("agents: {alpha: Alpha}\nfiled:\n  - {agent: alpha, id: ':404', step: s}\ntasks:\n  - {name: T, steps: [{id: s, title: S}]}\n")
    out = repo / "out" / "page.html"
    assert cli.main(["compare", "--out", str(out), "--notes", str(repo / "notes.yaml")]) in (0, None)
    assert "<h2>T</h2>" in out.read_text()
    printed = capsys.readouterr().out
    assert "matched no message" in printed and f"Wrote {out}" in printed
