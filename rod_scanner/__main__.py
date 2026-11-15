"""Entry point for running `python -m rod_scanner`."""

from __future__ import annotations

import sys

from rod_scanner.cli import main

if __name__ == "__main__":
    sys.exit(main())
