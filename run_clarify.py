#!/usr/bin/env python3
"""命令行启动器：在隔离模式（-I）下显式加入项目根目录。

用法：
    python3 -I run_clarify.py data/records.json positions
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from clarify.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
