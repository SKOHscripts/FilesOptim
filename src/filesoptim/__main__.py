"""Allow ``python -m filesoptim``."""

import sys

from filesoptim.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised through the CLI tests
    sys.exit(main())
