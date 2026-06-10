"""PyInstaller runtime hook — default the bundled binary to --ws mode.

The bundle is a GUI app: it serves the bundled web client over a
local HTTP/WebSocket port and the user interacts via their browser.
CLI mode requires an attached terminal that a double-clicked binary
does not have, so we inject --ws into sys.argv unless the user has
explicitly asked for help or already passed --ws themselves.

Runtime hooks run before the entry script, so agent.py's argparse
sees the modified argv as if --ws had been provided at the command
line.
"""
import sys

_HELP_FLAGS = {"-h", "--help"}

if not any(arg == "--ws" for arg in sys.argv[1:]) and not any(
    arg in _HELP_FLAGS for arg in sys.argv[1:]
):
    sys.argv.insert(1, "--ws")
