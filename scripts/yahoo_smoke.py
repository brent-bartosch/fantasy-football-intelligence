#!/usr/bin/env python3
"""Preflight: prove Yahoo auth + API access work. Part of the draft-day runbook later."""
from ffi.yahoo_client import get_session, get_league

LMU_2025 = "461.l.863132"  # from legacy import_all_lmu.py
session = get_session()
lg = get_league(session, LMU_2025)
settings = lg.settings()
print(
    f"OK: league '{settings.get('name')}' season {settings.get('season')} "
    f"num_teams={settings.get('num_teams')}"
)
