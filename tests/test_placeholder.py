"""Placeholder smoke test.

Verifies the pytest toolchain is wired up correctly. Will be replaced in
phase 0.3 with a proper `import ml4t.india` smoke test once the package
scaffolding lands.
"""

from __future__ import annotations

import sys


def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 12), (
        f"ml4t-india requires Python 3.12+, got {sys.version_info}"
    )
