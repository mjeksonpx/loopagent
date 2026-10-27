"""Allow `python -m loopgate <command>` without installing the package."""

import sys

from loopgate.cli import main

if __name__ == "__main__":
    sys.exit(main())
