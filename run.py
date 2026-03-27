"""Entry point - run the Insurance Automation system.

Usage:
    python run.py                         # watch mode (monitors watch/ folder)
    python run.py watch/test_case         # process a specific case folder
    python run.py path/to/any/case_folder # process any folder
"""

import sys
from src.main import main

if __name__ == "__main__":
    # Allow positional arg as shorthand: python run.py <case_dir>
    if len(sys.argv) == 2 and not sys.argv[1].startswith("--"):
        sys.argv = [sys.argv[0], "--process", sys.argv[1]]
    main()
