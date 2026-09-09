"""Original perspective-grid farewell; no third-party animation code or frames."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

MESSAGE = "thank you goodbye"
TEXTURE_TILES = ("MW88##%%@@##88WM", "[]{}<>024680<>{}", "NX7+=::=+7XN##%%")


def horizon_frame(columns: int, rows: int, phase: float) -> list[str]:
    """Return a terminal-sized ASCII plane with stationary text at its horizon."""
    width, height = max(1, columns), max(1, rows)
    horizon = height // 2
    lines = []
    for y in range(height):
        distance = abs(y - horizon)
        if y == horizon:
            line = "-" * width
        else:
            # Project moving texture through the exact center without fixed rails.
            center = (width - 1) / 2
            depth = height * 2 / distance
            band = int(depth)
            direction = 1 if y < horizon else -1
            chars = []
            for x in range(width):
                lateral = (x - center) / distance
                # Groups of glyphs grow toward the viewer and shrink to the
                # horizon. A narrow gap between blocks keeps the density legible.
                texel = math.floor((lateral - direction * phase * 2.1) * 4)
                block, cell = divmod(texel, 18)
                tile = TEXTURE_TILES[(block + band) % len(TEXTURE_TILES)]
                glyph = tile[(cell + band * 3) % len(tile)] if cell < 16 else " "
                chars.append(glyph)
            line = "".join(chars)
        lines.append(line)
    text = (" " + MESSAGE + " ") if width >= len(MESSAGE) + 2 else MESSAGE[:width]
    start = max(0, (width - len(text)) // 2)
    lines[horizon] = lines[horizon][:start] + text + lines[horizon][start + len(text) :]
    return lines


def colored_horizon(lines: list[str]) -> str:
    """Cobalt edges fade to an icy-white horizon; geometry remains plain ASCII."""
    horizon = len(lines) // 2
    blues = (27, 33, 39, 75, 117, 153, 195, 231)
    result = []
    for row, line in enumerate(lines):
        proximity = 1 - abs(row - horizon) / max(horizon, 1)
        color = blues[round(max(0, proximity) * (len(blues) - 1))]
        result.append(f"\x1b[38;5;{color}m{line}")
    return "\r\n".join(result)


@contextmanager
def terminal_keys():
    """Enable nonblocking key reads and restore the exact original console modes."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        output = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel.GetConsoleMode(output, ctypes.byref(mode)):
            raise OSError("Console mode unavailable")
        if not kernel.SetConsoleMode(output, mode.value | 4):
            raise OSError("ANSI output unavailable")
        try:
            yield lambda: msvcrt.getwch() if msvcrt.kbhit() else ""
        finally:
            kernel.SetConsoleMode(output, mode.value)
    else:
        import select
        import termios
        import tty

        descriptor = sys.stdin.fileno()
        original = termios.tcgetattr(descriptor)
        try:
            tty.setcbreak(descriptor)
            yield (
                lambda: (
                    os.read(descriptor, 1).decode("ascii", errors="ignore")
                    if select.select([descriptor], [], [], 0)[0]
                    else ""
                )
            )
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, original)


def run_animation(
    read_key, *, size=shutil.get_terminal_size, sleep=time.sleep, output=None
) -> bool:
    """Animate until an explicit Y + Enter; N continues. Dependencies allow deterministic tests."""
    output = output or sys.stdout
    started = time.monotonic()
    answer = ""
    previous_size = None
    confirmed = False
    output.write("\x1b[?1049h\x1b[?25l")
    try:
        while True:
            key = read_key()
            if key in ("\x03", "\x04"):
                break
            if key in ("\r", "\n"):
                if answer.lower() == "y":
                    confirmed = True
                    break
                answer = ""
            elif key.lower() in ("y", "n"):
                answer = key.lower()
            elif key in ("\b", "\x7f"):
                answer = ""
            dimensions = size()
            width, height = max(1, dimensions.columns - 1), max(1, dimensions.lines - 3)
            if dimensions != previous_size:
                output.write("\x1b[2J")
                previous_size = dimensions
            lines = horizon_frame(width, height, time.monotonic() - started)
            prompt = f"Close terminal? [y/n] {answer.upper() or '_'}"
            if width < len(prompt):
                prompt = f"Close? y/n {answer.upper() or '_'}"
            output.write(
                "\x1b[?25l\x1b[H"
                + colored_horizon(lines)
                + "\r\n\x1b[0m\x1b[97m"
                + prompt[:width].ljust(width)
                + f"\x1b[{height + 1};{min(len(prompt) + 1, width)}H\x1b[?25h"
            )
            output.flush()
            sleep(1 / 12)
    except KeyboardInterrupt:
        pass
    finally:
        output.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        output.flush()
    return confirmed


def close_launcher_terminal() -> None:
    """After launcher exit, close only its idle, single-tab Apple Terminal window."""
    if (
        sys.platform != "darwin"
        or os.environ.get("STOCKRANK_DESKTOP_LAUNCHER") != "1"
        or os.environ.get("TERM_PROGRAM") != "Apple_Terminal"
        or not sys.stdin.isatty()
    ):
        return
    try:
        terminal = os.ttyname(sys.stdin.fileno())
        # Wait for the launcher shell to exit before closing, avoiding Terminal's
        # running-process confirmation. Never target the front/active window.
        script = """on run argv
    set targetTTY to item 1 of argv
    repeat 60 times
        tell application id "com.apple.Terminal"
            repeat with candidate in windows
                if (count of tabs of candidate) is 1 then
                    set targetTab to first tab of candidate
                    if tty of targetTab is targetTTY then
                        if not busy of targetTab then
                            close candidate saving no
                            return
                        end if
                    end if
                end if
            end repeat
        end tell
        delay 0.5
    end repeat
end run"""
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script, terminal],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            start_new_session=True,
        )
    except OSError:
        print("Automatic Terminal close unavailable; you can close this window manually.")


def show_farewell() -> None:
    """Never block redirected output, CI, or unsupported consoles on an animation."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("CI")
    if interactive and os.environ.get("TERM") != "dumb":
        try:
            with terminal_keys() as read_key:
                confirmed = run_animation(read_key)
            print(MESSAGE)
            if confirmed:
                close_launcher_terminal()
            return
        except (OSError, ValueError):
            pass
    print("\n".join(horizon_frame(62, 11, 0)))
    if interactive:
        try:
            while input("Close terminal? [y/n] ").strip().lower() != "y":
                pass
            close_launcher_terminal()
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    show_farewell()
