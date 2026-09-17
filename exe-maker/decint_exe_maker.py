#!/usr/bin/env python3
"""Start DECINT EXE Maker.

    python decint_exe_maker.py                 # GUI
    python decint_exe_maker.py build ...       # CLI (see --help)
"""
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from decint_exe_maker.__main__ import entry
    sys.exit(entry())
