import datetime
import pathlib

import pytest

from ffi.flags import UnknownModuleError, disabled_since, enabled

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULES = REPO_ROOT / "config" / "modules.yaml"


def test_known_modules_enabled_by_default():
    assert enabled("usage_trends", MODULES) is True
    assert enabled("waiver_advisor", MODULES) is True
    assert enabled("trade_angles", MODULES) is True
    assert enabled("push", MODULES) is True


def test_enabled_module_has_no_disabled_since():
    assert disabled_since("usage_trends", MODULES) is None


def test_unknown_module_raises_rather_than_defaulting():
    with pytest.raises(UnknownModuleError, match="bogus"):
        enabled("bogus", MODULES)


def test_disabled_module_flags_and_records_date(tmp_path):
    p = tmp_path / "modules.yaml"
    p.write_text(
        "modules:\n"
        "  push:\n"
        "    enabled: false\n"
        "    disabled_since: 2026-08-30\n"
    )
    assert enabled("push", p) is False
    assert disabled_since("push", p) == datetime.date(2026, 8, 30)


def test_missing_module_key_fails_loud(tmp_path):
    p = tmp_path / "modules.yaml"
    p.write_text("modules: {}\n")
    with pytest.raises(UnknownModuleError, match="push"):
        enabled("push", p)


def test_missing_modules_key_fails_loud(tmp_path):
    p = tmp_path / "modules.yaml"
    p.write_text("as_of: 2026-09-02\n")
    with pytest.raises(ValueError, match="modules"):
        enabled("push", p)
