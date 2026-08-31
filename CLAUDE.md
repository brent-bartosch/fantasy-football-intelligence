# fantasy_football

Architecture contract: see ARCHITECTURE.md (module boundaries, dependency direction, forbidden imports). Read before writing code.

- CI guards: `scripts/check_file_size.sh`, `scripts/check_import_boundaries.py`, `scripts/check_no_secrets.sh` — run all three before committing.
- Ceremony chain for the in-season ops build: design `docs/superpowers/specs/2026-08-30-in-season-ops-design.md` → risks + ADR in `docs/superpowers/risks/` → ARCHITECTURE.md → implementation plan in `docs/superpowers/plans/`.
- House rules: fail-loud (no bare `except:`, no silent fallbacks — invoke fail-loud-error-handling when writing any), Postgres only via `src/ffi/db.py`, Yahoo HTTP only via `src/ffi/yahoo_client.py`, pushes only via `scripts/notify.py`.
