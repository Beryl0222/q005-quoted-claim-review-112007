#!/usr/bin/env python3
"""隔离模式下的测试启动器：显式加入项目根目录后运行 unittest。

用法：python3 -I run_tests.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
