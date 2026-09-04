"""Single reader of `config/modules.yaml` (per-module enable flags, ADR D8
rollback unit).

Layer-0 leaf: imports nothing internal. The rollback switch must be readable
by a module that is itself never disabled — if `flags.py` imported `db` or
`health`, flipping a flag could take down the very thing that reads the flag.

The contract (ARCHITECTURE §4): `enabled(module) -> bool`,
`disabled_since(module) -> date | None`. A disabled module is as visible as a
broken one — its report section renders `OFF (disabled <date>)`, never vanishes.
"""
from __future__ import annotations

import datetime
import pathlib

import yaml

DEFAULT_PATH = pathlib.Path("config/modules.yaml")


class UnknownModuleError(KeyError):
    """A module has no entry in config/modules.yaml.

    Deliberately not a default-enabled contract: an unregistered module is an
    un-flaggable module, and silently treating it as enabled defeats the
    rollback unit's whole purpose.
    """


def _modules(path: pathlib.Path | None = None) -> dict:
    p = path or DEFAULT_PATH
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict) or "modules" not in raw:
        raise ValueError(f"{p}: expected a mapping with a 'modules' key")
    return raw["modules"] or {}


def _block(module: str, path: pathlib.Path | None = None) -> dict:
    mods = _modules(path)
    block = mods.get(module)
    if block is None:
        raise UnknownModuleError(
            f"unknown module {module!r} in config/modules.yaml — known: {sorted(mods)}"
        )
    if not isinstance(block, dict):
        raise ValueError(f"config/modules.yaml: module {module!r} block is not a mapping")
    return block


def enabled(module: str, path: pathlib.Path | None = None) -> bool:
    """True unless the module's `enabled` flag is explicitly False."""
    return bool(_block(module, path).get("enabled", True))


def disabled_since(
    module: str, path: pathlib.Path | None = None
) -> datetime.date | None:
    """The `disabled_since` date for a module, or None if it is not disabled."""
    ds = _block(module, path).get("disabled_since")
    if ds is None:
        return None
    if isinstance(ds, datetime.date):
        return ds
    return datetime.date.fromisoformat(str(ds))
