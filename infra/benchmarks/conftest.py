"""sys.path and environment setup for benchmarks that import from the
examples directory (e.g. sse_dashboard).

This file is automatically loaded when running benchmarks via
``python -m infra.benchmarks.run`` or ``pytest infra/benchmarks/``.
"""

from __future__ import annotations

import os
import sys

# Ensure the examples/langchain-support-agent package is importable
# so benchmarks can use SSEConnection without path manipulation.
_examples_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "examples",
    "langchain-support-agent",
)
if _examples_path not in sys.path:
    sys.path.insert(0, _examples_path)
