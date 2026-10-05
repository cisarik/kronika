"""Retained module spelling of the NUC release-update engine.

The canonical engine is ``deploy/ubuntu/kronika_release.py`` (ADR-0085). This
file is the retained compatibility name for that same engine: importing it
exposes the canonical engine's own names, and running it forwards every argument
to the canonical engine under the same interpreter. It is not a second
deployment system, and it is removed together with
``deploy/ubuntu/framenest-release`` in the compatibility-removal cut.

This module is an entry point, not a second engine. It holds its own copy of the
canonical module's namespace, so a caller that needs to replace an engine
constant must do so on ``kronika_release`` itself.

The engine is loaded by path rather than imported as a package, because it runs
from the Git checkout under Ubuntu system Python with the application package
absent from ``sys.path``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType

CANONICAL_ENGINE_PATH = Path(__file__).resolve().with_name("kronika_release.py")


def _load_canonical_engine() -> ModuleType:
    if not CANONICAL_ENGINE_PATH.is_file():
        print("Kronika release engine is unavailable.", file=sys.stderr)
        raise SystemExit(1)
    spec = importlib.util.spec_from_file_location(
        "kronika_release", CANONICAL_ENGINE_PATH
    )
    if spec is None or spec.loader is None:
        print("Kronika release engine is unavailable.", file=sys.stderr)
        raise SystemExit(1)
    engine = importlib.util.module_from_spec(spec)
    sys.modules["kronika_release"] = engine
    spec.loader.exec_module(engine)
    return engine


def _run_canonical_engine() -> None:
    """Replace this process with the canonical engine under the same interpreter.

    Exactly one interpreter then runs the engine, and its exit status is the
    status this retained entry point returns.
    """
    os.execv(
        sys.executable,
        [sys.executable, str(CANONICAL_ENGINE_PATH), *sys.argv[1:]],
    )


_run = _run_canonical_engine

_canonical = _load_canonical_engine()
globals().update(
    {
        name: value
        for name, value in vars(_canonical).items()
        if name not in ("__name__", "__file__", "__doc__", "__spec__", "__loader__")
    }
)

if __name__ == "__main__":
    _run()