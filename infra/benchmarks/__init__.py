"""UndoLog benchmark scripts.

Benchmarks characterise end-to-end latency, throughput, dedup overhead,
and resource consumption for production sizing and regression detection.
"""

from __future__ import annotations

import os
import sys

# Ensure the examples/langchain-support-agent package is importable
# so benchmarks can use SSEConnection without per-function path hacks.
_examples_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "examples",
    "langchain-support-agent",
)
if _examples_path not in sys.path:
    sys.path.insert(0, _examples_path)
