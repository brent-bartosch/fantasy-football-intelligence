#!/usr/bin/env python3
"""Import-boundary guard for fantasy_football.

Enforces ARCHITECTURE.md:
  * Section 3 ("Forbidden imports") is parsed from the file itself. Keep the bullet
    format machine-parseable — exactly one rule per bullet:

        - `from_glob` -> `to_glob` - rationale        (with a real arrow and em dash)

    `*` is a wildcard. Left side is a repo-relative path glob (which files the rule
    applies to). Right side is either a repo-relative path glob (converted to a dotted
    module prefix) or a bare module name.
  * Section 3a ("Non-import rules") is NOT parsed — those four rules are hard-coded
    below as AST/text checks because they are call-site rules, not import-graph rules:
      R-HTTP     HTTP libraries only in yahoo_client.py, ingest/, notify.py, probe script
      R-PGCONN   psycopg connect() only in src/ffi/db.py
      R-EXCEPT   no bare `except:` anywhere
      R-CAPTURE  only src/ffi/league_state/ may touch data/captures/

Usage:  python3 scripts/check_import_boundaries.py       (exit 1 on any violation)
        python3 scripts/check_import_boundaries.py --list-debt   (show the allowlist)

GRANDFATHER ALLOWLIST
---------------------
Pre-existing violations recorded on 2026-08-31 when this contract was written. They are
documented in ARCHITECTURE.md §3b "Known debt". New files get no grace. Do not extend
these lists — shrink them. Five of the HTTP entries disappear when ADR Domain 4 moves the
legacy auth scripts to scripts/archive/.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "ARCHITECTURE.md"

SCAN_DIRS = ("src", "scripts")
SKIP_PARTS = {"__pycache__", ".venv", "archive", "node_modules", ".git"}

# --- Grandfathered pre-existing violations (see module docstring) --------------------
ALLOWLIST: dict[str, set[str]] = {
    "R-EXCEPT": {
        "scripts/import_league_326814.py",
        "scripts/yahoo_auth_simple.py",
        "scripts/yahoo_manual_auth.py",
    },
    "R-PGCONN": {
        "scripts/import_all_lmu.py",
        "scripts/import_league_326814.py",
        "scripts/import_yahoo_data.py",
        "scripts/ingest_expert_rankings.py",
        "scripts/rss_ingester.py",
        "scripts/scoring_adjuster.py",
        "scripts/update_player_names.py",
    },
    "R-HTTP": {
        "scripts/import_all_lmu.py",
        "scripts/import_league_326814.py",
        "scripts/import_yahoo_data.py",
        "scripts/ingest_expert_rankings.py",
        "scripts/list_my_leagues.py",
        "scripts/setup_yahoo_auth.py",
        "scripts/source_backtest_archives.py",
        "scripts/yahoo_auth.py",
        "scripts/yahoo_auth_simple.py",
        "scripts/yahoo_manual_auth.py",
    },
    "R-CAPTURE": set(),
}

# --- Hard-coded non-import rules -----------------------------------------------------
HTTP_MODULES = (
    "requests",
    "requests_oauthlib",
    "httpx",
    "aiohttp",
    "urllib.request",
    "yahoo_oauth",
)
HTTP_ALLOWED = (
    "src/ffi/yahoo_client.py",
    "src/ffi/ingest/*",
    "scripts/notify.py",
    "scripts/probe_yahoo_access.py",
)
PG_MODULES = ("psycopg2", "psycopg")
PG_ALLOWED = ("src/ffi/db.py",)
CAPTURE_TOKEN = "data/captures"
CAPTURE_ALLOWED = ("src/ffi/league_state/*", "scripts/check_import_boundaries.py")

BULLET_RE = re.compile(
    r"^-\s+`(?P<src>[^`]+)`\s*(?:→|->)\s*`(?P<dst>[^`]+)`\s*(?:—|--|-)\s*(?P<why>.+)$"
)


def matches_module(name: str, target: str) -> bool:
    return name == target or name.startswith(target + ".")


def path_matches(rel: str, glob: str) -> bool:
    if glob == "*":
        return True
    if glob.endswith("/*"):
        return rel == glob[:-2] or rel.startswith(glob[:-2] + "/")
    return fnmatch.fnmatch(rel, glob)


def glob_to_module_targets(glob: str) -> list[str]:
    """Convert a right-hand-side path glob into dotted module prefixes."""
    g = glob.strip()
    if "/" not in g:
        return [g]  # bare module name, e.g. `psycopg2`
    g = g[:-2] if g.endswith("/*") else g
    g = g.rstrip("/")
    if g.endswith(".py"):
        g = g[:-3]
    if g.startswith("src/"):
        return [g[len("src/") :].replace("/", ".")]
    if g.startswith("scripts"):
        # scripts are not an importable package; catch both `scripts.x` and bare `x`
        targets = ["scripts"]
        rest = g[len("scripts") :].lstrip("/")
        if rest:
            targets.append("scripts." + rest.replace("/", "."))
            targets.append(rest.replace("/", "."))
        else:
            targets.extend(
                p.stem for p in (ROOT / "scripts").glob("*.py") if p.stem != "__init__"
            )
        return targets
    return [g.replace("/", ".")]


def parse_forbidden(text: str) -> list[tuple[str, list[str], str, str]]:
    """Return (src_glob, [dst_module_prefixes], dst_glob_raw, rationale)."""
    rules: list[tuple[str, list[str], str, str]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.lower().startswith("## 3. forbidden imports")
            continue
        if line.startswith("### "):
            # 3a/3b are documentation + hard-coded rules, not parsed bullets
            in_section = False
            continue
        if not in_section:
            continue
        m = BULLET_RE.match(line.strip())
        if m:
            rules.append(
                (
                    m.group("src").strip(),
                    glob_to_module_targets(m.group("dst")),
                    m.group("dst").strip(),
                    m.group("why").strip(),
                )
            )
    return rules


def iter_py_files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if SKIP_PARTS & set(p.relative_to(ROOT).parts):
                continue
            out.append(p)
    return sorted(out)


def file_package(rel: str) -> str:
    """Dotted package of the file's directory, for resolving relative imports."""
    parts = rel.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts[:-1])


def imported_names(tree: ast.AST, rel: str):
    """Yield (dotted_name, lineno) for every import in the module."""
    pkg = file_package(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                drop = node.level - 1
                base = base[: len(base) - drop] if drop else base
                mod = ".".join(
                    [p for p in base if p] + ([node.module] if node.module else [])
                )
            else:
                mod = node.module or ""
            if mod:
                yield mod, node.lineno
                for a in node.names:
                    yield f"{mod}.{a.name}", node.lineno


def allowed(rule: str, rel: str) -> bool:
    return rel in ALLOWLIST.get(rule, set())


def main(argv: list[str]) -> int:
    if "--list-debt" in argv:
        for rule, paths in sorted(ALLOWLIST.items()):
            print(f"{rule}: {len(paths)} grandfathered")
            for p in sorted(paths):
                print(f"    {p}")
        return 0

    if not ARCH.exists():
        print(f"FAIL: {ARCH} not found — no contract to enforce.", file=sys.stderr)
        return 1

    rules = parse_forbidden(ARCH.read_text(encoding="utf-8"))
    if not rules:
        print(
            "FAIL: parsed 0 forbidden-import rules from ARCHITECTURE.md §3.",
            file=sys.stderr,
        )
        print(
            "      Check the bullet format: - `a/*` -> `b/*` - rationale",
            file=sys.stderr,
        )
        return 1

    violations: list[str] = []
    files = iter_py_files()

    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src, filename=rel)
        except SyntaxError as exc:  # a file that cannot be parsed is a hard failure
            violations.append(f"{rel}:{exc.lineno}: SYNTAX — {exc.msg}")
            continue

        imports = list(imported_names(tree, rel))

        # Section 3: parsed forbidden imports
        for src_glob, targets, dst_raw, why in rules:
            if not path_matches(rel, src_glob):
                continue
            for name, lineno in imports:
                if any(matches_module(name, t) for t in targets):
                    violations.append(
                        f"{rel}:{lineno}: FORBIDDEN IMPORT `{name}` "
                        f"({src_glob} -> {dst_raw}) — {why}"
                    )
                    break

        # R-HTTP
        if not any(path_matches(rel, g) for g in HTTP_ALLOWED) and not allowed(
            "R-HTTP", rel
        ):
            for name, lineno in imports:
                if any(matches_module(name, t) for t in HTTP_MODULES):
                    violations.append(
                        f"{rel}:{lineno}: R-HTTP — `{name}` outside the transport allowlist "
                        f"(yahoo_client.py, ingest/, notify.py, probe_yahoo_access.py). "
                        f"See ARCHITECTURE.md §3a."
                    )
                    break

        # R-PGCONN
        if not any(path_matches(rel, g) for g in PG_ALLOWED) and not allowed(
            "R-PGCONN", rel
        ):
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "connect":
                        base = node.func.value
                        root = base.id if isinstance(base, ast.Name) else None
                        if root in PG_MODULES:
                            violations.append(
                                f"{rel}:{node.lineno}: R-PGCONN — direct `{root}.connect()`. "
                                f"Only src/ffi/db.py opens Postgres connections; use "
                                f"`ffi.db.connect()`. See ARCHITECTURE.md §3a."
                            )

        # R-EXCEPT
        if not allowed("R-EXCEPT", rel):
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    violations.append(
                        f"{rel}:{node.lineno}: R-EXCEPT — bare `except:`. Fail loud: catch the "
                        f"specific exception or let it propagate. See ARCHITECTURE.md §7."
                    )

        # R-CAPTURE
        if not any(path_matches(rel, g) for g in CAPTURE_ALLOWED) and not allowed(
            "R-CAPTURE", rel
        ):
            for i, line in enumerate(src.splitlines(), start=1):
                if CAPTURE_TOKEN in line and not line.lstrip().startswith("#"):
                    violations.append(
                        f"{rel}:{i}: R-CAPTURE — reads `{CAPTURE_TOKEN}`. Only "
                        f"src/ffi/league_state/ touches captures (ts_precision is attached "
                        f"in exactly one place). See ARCHITECTURE.md §3a."
                    )
                    break

    if violations:
        for v in violations:
            print(v)
        print(
            f"\nFAIL: {len(violations)} boundary violation(s) across {len(files)} files."
        )
        return 1

    print(
        f"OK: {len(files)} files clean against {len(rules)} parsed forbidden-import rules "
        f"+ 4 hard-coded rules "
        f"({sum(len(v) for v in ALLOWLIST.values())} grandfathered paths allowlisted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
