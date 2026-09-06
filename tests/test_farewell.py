import io
import os

import pytest

from stockrank import farewell


@pytest.mark.parametrize("width,height", [(1, 1), (16, 5), (79, 21), (119, 37), (159, 45)])
def test_frames_fit_and_message_stays_at_horizon(width, height):
    first = farewell.horizon_frame(width, height, 0)
    second = farewell.horizon_frame(width, height, 0.3)
    assert len(first) == height
    assert {len(line) for line in first} == {width}
    assert all(line.isascii() for line in first)
    assert first[height // 2] == second[height // 2]
    if width >= len(farewell.MESSAGE):
        assert farewell.MESSAGE in first[height // 2]
    if width > 20:
        assert first != second


def test_animation_resizes_and_n_continues_until_y_enter():
    keys = iter(["", "n", "\r", "", "y", "\r"])
    sizes = iter(
        [
            os.terminal_size((80, 24)),
            os.terminal_size((100, 30)),
            os.terminal_size((100, 30)),
            os.terminal_size((100, 30)),
            os.terminal_size((100, 30)),
        ]
    )
    output = io.StringIO()
    frames = []
    farewell.run_animation(
        lambda: next(keys),
        size=lambda: next(sizes),
        sleep=lambda seconds: frames.append(seconds),
        output=output,
    )
    assert len(frames) == 5
    assert output.getvalue().count("\x1b[2J") == 2
    assert "Close terminal? [y/n] N" in output.getvalue()
    assert "Close terminal? [y/n] Y" in output.getvalue()
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h\x1b[?1049l")


def test_animation_restores_terminal_on_interrupt():
    output = io.StringIO()
    farewell.run_animation(lambda: "\x03", output=output)
    assert "\x1b[?25h" in output.getvalue()


def test_redirected_farewell_does_not_read_input(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Noninteractive input blocked"))
    farewell.show_farewell()
    assert "thank you goodbye" in capsys.readouterr().out


def test_both_halves_animate_and_color_fades_toward_white():
    first = farewell.horizon_frame(79, 21, 0)
    second = farewell.horizon_frame(79, 21, 0.3)
    assert first[:10] != second[:10]
    assert first[11:] != second[11:]
    colored = farewell.colored_horizon(first)
    assert "\x1b[38;5;27m" in colored
    assert "\x1b[38;5;231m" in colored
    assert farewell.MESSAGE in colored


def test_planes_slide_in_opposite_directions_around_fixed_horizon():
    first = farewell.horizon_frame(119, 37, 0)
    second = farewell.horizon_frame(119, 37, 1)
    # At ten rows from the horizon, one second moves the mesh ten columns.
    assert second[8][:-10] == first[8][10:]
    assert second[28][10:] == first[28][:-10]
    assert second[18] == first[18]
    assert second[8] != first[8]
    assert second[28] != first[28]


def test_desktop_success_does_not_add_another_prompt(monkeypatch):
    from stockrank import desktop_launcher

    calls = []
    monkeypatch.setattr(desktop_launcher.cli, "main", lambda args: calls.append(args) or 0)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Duplicate success prompt"))
    assert desktop_launcher.main() == 0
    assert calls == [["morning"]]


def test_desktop_failure_remains_visible(monkeypatch):
    from stockrank import desktop_launcher

    prompts = []
    monkeypatch.setattr(desktop_launcher.cli, "main", lambda args: 2)
    monkeypatch.setattr(desktop_launcher.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda message: prompts.append(message))
    assert desktop_launcher.main() == 2
    assert prompts == ["Press Enter to close..."]
