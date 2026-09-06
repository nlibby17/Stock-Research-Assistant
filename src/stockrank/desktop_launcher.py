"""Own the Windows desktop console without a waiting batch interpreter."""

from __future__ import annotations

import sys
import traceback

from stockrank import cli
from stockrank.farewell import show_farewell


def main() -> int:
    try:
        result = cli.main(["morning"])
    except KeyboardInterrupt:
        print("\nWorkflow stopped.")
        show_farewell()
        result = 0
    except Exception:  # noqa: BLE001 - keep unexpected launcher failures visible
        traceback.print_exc()
        result = 1
    if result and sys.stdin.isatty():
        print("\nStock Research Assistant requires attention. Review the messages above.")
        try:
            input("Press Enter to close...")
        except (EOFError, KeyboardInterrupt):
            pass
    # The dashboard farewell already handles the one successful Y + Enter.
    return result


if __name__ == "__main__":
    raise SystemExit(main())
