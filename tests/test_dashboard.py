import io

from smart_ralph.dashboard import Dashboard


def test_non_tty_falls_back_to_plain_one_line_per_event():
    buf = io.StringIO()  # not a TTY — isatty() is False
    dashboard = Dashboard(stream=buf)

    assert dashboard.mode == "plain"

    dashboard.emit({
        "type": "run_started",
        "source": "supervisor",
        "issue": 2,
        "payload": {"prd": "2"},
    })
    dashboard.emit({
        "type": "ralph_stdout",
        "source": "ralph",
        "issue": 2,
        "payload": {"line": "iteration: start"},
    })

    out = buf.getvalue()
    lines = out.splitlines()
    assert len(lines) == 2
    assert "run_started" in lines[0]
    assert "iteration: start" in lines[1]


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_attached_mode_renders_split_pane_without_crashing():
    tty = _FakeTTY()
    dashboard = Dashboard(stream=tty, force_mode="attached")

    assert dashboard.mode == "attached"

    events = [
        {"type": "run_started", "source": "supervisor", "issue": 2, "payload": {"prd": "2"}},
        {"type": "ralph_spawned", "source": "supervisor", "issue": 2, "payload": {"pid": 1234}},
        {"type": "ralph_stdout", "source": "ralph", "issue": 2, "payload": {"line": "step 1"}},
        {"type": "ralph_stdout", "source": "ralph", "issue": 2, "payload": {"line": "step 2"}},
        {"type": "ralph_exited", "source": "supervisor", "issue": 2, "payload": {"exit_code": 0}},
        {"type": "run_ended", "source": "supervisor", "issue": 2, "payload": {"exit_code": 0}},
    ]
    dashboard.render(events)

    out = tty.getvalue()
    # split-pane labels show up somewhere in the rendered output
    assert "Progress" in out
    assert "Ralph output" in out
    # latest stdout line landed in the bottom pane
    assert "step 2" in out
    # issue number surfaces in the top pane
    assert "2" in out


def test_anomaly_detected_renders_prominent_in_attached_mode():
    """Anomalies surface in the Progress panel with the rule name and
    a red marker so the operator can't miss them."""
    tty = _FakeTTY()
    dashboard = Dashboard(stream=tty, force_mode="attached")
    events = [
        {"type": "run_started", "source": "supervisor", "issue": 7,
         "payload": {}},
        {"type": "ralph_exited", "source": "supervisor", "issue": 7,
         "payload": {"exit_code": 1}},
        {"type": "anomaly_detected", "source": "supervisor", "issue": 7,
         "payload": {
             "rule": "ralph_nonzero_exit",
             "evidence": {"exit_code": 1, "log_tail": []},
         }},
    ]

    dashboard.render(events)

    out = tty.getvalue()
    assert "ralph_nonzero_exit" in out
    # Rich emits ANSI escapes for red — accept either the SGR code or a
    # literal "anomaly" marker word so the test isn't tied to one renderer.
    assert ("\x1b[31m" in out) or ("\x1b[91m" in out) or ("ANOMALY" in out.upper())


def test_anomaly_detected_in_plain_mode_writes_one_line():
    buf = io.StringIO()
    dashboard = Dashboard(stream=buf)

    dashboard.emit({
        "type": "anomaly_detected", "source": "supervisor", "issue": 7,
        "payload": {"rule": "ralph_nonzero_exit",
                    "evidence": {"exit_code": 1}},
    })

    line = buf.getvalue().splitlines()[0]
    assert "anomaly_detected" in line
    assert "ralph_nonzero_exit" in line
