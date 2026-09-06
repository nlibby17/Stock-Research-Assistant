import io
import os
import re
import subprocess

import pytest

from stockrank import daily_workflow as workflow


def plain(frame):
    return re.sub(r"\x1b\[[0-9;]*m", "", frame)


def test_line_reveals_left_to_right_and_loops_without_changing_labels():
    original = workflow.WELCOME_ART.splitlines()
    width = workflow.WELCOME_LINE_END - workflow.WELCOME_LINE_START
    for step in range(width + 1):
        frame = workflow.welcome_frame(step)
        rows = plain(frame).splitlines()
        assert rows[4] == original[4]
        for before, after in zip(original[:4], rows[:4]):
            assert after[:6] == before[:6]
            assert after[26:] == before[26:]
            graph = before[6:26]
            assert after[6:26] == graph[:step] + " " * max(0, len(graph) - step)
        # Green is restricted to graph columns, never the axes or message.
        for row in frame.splitlines()[:4]:
            assert row.index(workflow.WELCOME_GREEN) == 6
            assert "Thank you" not in row.split(workflow.WELCOME_GREEN)[1].split("\x1b[0m")[0]
    assert plain(workflow.welcome_frame(width)) == workflow.WELCOME_ART
    assert workflow.welcome_frame(width + 1) == workflow.welcome_frame(0)


def test_animation_waits_half_a_second_per_section_and_returns_process_exit(monkeypatch):
    monkeypatch.setattr(workflow.shutil, "get_terminal_size", lambda: os.terminal_size((80, 24)))
    waits = []

    class Process:
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) <= 22:
                raise subprocess.TimeoutExpired("dashboard", timeout)
            return 7

    output = io.StringIO()
    assert workflow.animate_welcome(Process(), output=output) == 7
    assert waits == [0.5] * 23
    assert output.getvalue().count(workflow.welcome_frame(0) + "\n") == 2
    assert output.getvalue().endswith("\x1b[0m")


def test_interrupt_resets_color_and_reaches_existing_shutdown_handler():
    class Process:
        def wait(self, timeout=None):
            raise KeyboardInterrupt

    output = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        workflow.animate_welcome(Process(), output=output)
    assert output.getvalue().endswith("\x1b[0m")


def test_redirected_output_is_static(monkeypatch, capsys):
    monkeypatch.setattr(workflow.sys.stdout, "isatty", lambda: False)

    class Process:
        def wait(self, timeout=None):
            assert timeout is None
            return 0

    assert workflow.wait_with_welcome(Process()) == 0
    assert capsys.readouterr().out == workflow.WELCOME_ART + "\n"


def test_shrinking_terminal_stops_repainting(monkeypatch):
    monkeypatch.setattr(workflow.shutil, "get_terminal_size", lambda: os.terminal_size((40, 10)))

    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("dashboard", timeout)
            return 0

    output = io.StringIO()
    assert workflow.animate_welcome(Process(), output=output) == 0
    assert "\x1b[5A" not in output.getvalue()
