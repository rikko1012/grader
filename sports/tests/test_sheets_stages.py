"""A killed sheets run still names the step it died in; the last finished run stays readable."""
import json

from sports import sheets


def test_steps_are_on_disk_before_the_step_ends(tmp_path):
    p = tmp_path / "sheets_stages.json"
    st = sheets.Stages(p)
    st.run("sheets:nba", lambda: 1)
    seen = {}

    def dies():
        seen.update(json.loads(p.read_text(encoding="utf-8")))
        raise KeyboardInterrupt

    try:
        st.run("results", dies)
    except KeyboardInterrupt:
        pass
    assert seen["current"]["step"] == "results" and seen["current"]["finished"] is False
    assert [s[0] for s in seen["current"]["steps"]] == ["sheets:nba"] and seen["last_ok"] is None


def test_a_finished_run_survives_the_next_killed_one(tmp_path):
    p = tmp_path / "sheets_stages.json"
    st = sheets.Stages(p)
    st.run("sheets:nba", lambda: 1)
    st.done()
    st2 = sheets.Stages(p)
    st2._save("sheets:nba", False)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["last_ok"]["finished"] is True and d["current"]["finished"] is False
