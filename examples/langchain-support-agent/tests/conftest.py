"""Test configuration: add examples/ root to sys.path for example_tools imports."""

from __future__ import annotations

import os
import sys

_examples_root = os.path.join(os.path.dirname(__file__), "..", "..")
if _examples_root not in sys.path:
    sys.path.insert(0, _examples_root)
