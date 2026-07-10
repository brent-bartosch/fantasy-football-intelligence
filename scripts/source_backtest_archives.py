#!/usr/bin/env python3
"""Backtest archive sourcing attempt (R11 — Task 10).

Attempts, in a fixed order, to source preseason ADP/ECR and preseason season
projections for the 2023/2024/2025 seasons from the open web, and stores
whatever succeeds in raw.backtest_sources ((source, season, kind) upsert,
idempotent re-run). Kind is 'adp'|'projections'|'ecr'.

Order (per task-10-brief.md):
  1. dynastyprocess/data GitHub archive (db_fpecr.parquet) — weekly FantasyPros
     ECR snapshots since ~2021, including a PPR superflex/2QB overall redraft
     cheatsheet page. Kind 'ecr'.
  2. ffverse / dynastyprocess GitHub orgs for archived preseason *projection*
     (stat-line) CSVs — probed, nothing found (see research doc). No code
     path here because there was nothing to fetch.
  3. Wayback Machine snapshots of fantasypros.com/nfl/projections/{pos}.php
     ?week=draft (per-position season projection tables) for pos in
     qb/rb/wr/te/k, closest snapshot to Aug 20 of each season within an
     Aug 1 - Sep 20 window (the "preseason" proxy window). Kind 'projections'.

Documented failure is an acceptable outcome (risk register R11). This script
never stores a parse it cannot validate — see MIN_ADP_ROWS / MIN_PROJ_ROWS
and the fail-loud raises below. Partial-but-real coverage (e.g. a season
where only some positions had a preseason-window snapshot) IS stored,
honestly labeled with its row count and position coverage, so Task 11 can
use what's real and degrade only what's missing — see
docs/research/2026-07-10-backtest-archive-sourcing.md for the full matrix
and per-season verdicts.

Politeness: <=1 request/sec (REQUEST_SPACING_S), no auth'd scraping, no bulk
crawling — this hits maybe ~30 URLs total across one run.
"""
import io
import json
import re
import time
from datetime import date

import polars as pl
import requests

from ffi.db import connect

UA = {"User-Agent": "ffi-backtest-research/1.0 (one-shot archive sourcing script)"}
REQUEST_SPACING_S = 2.5  # archive.org CDX throttles bursts hard; stay well under

DYNASTYPROCESS_PARQUET_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_fpecr.parquet"
# PPR + superflex/2QB overall redraft cheatsheet ECR page (verified live 2026-07-10:
# 489-510 rows/season for 2023-2025, positions QB/RB/WR/TE all present).
DYNASTYPROCESS_FP_PAGE = "/nfl/rankings/ppr-superflex-cheatsheets.php"

FP_PROJECTIONS_URL = "https://www.fantasypros.com/nfl/projections/{pos}.php?week=draft"
PROJECTION_POSITIONS = ["qb", "rb", "wr", "te", "k"]

SEASONS = [2023, 2024, 2025]
MIN_ADP_ROWS = 150
MIN_PROJ_ROWS = 250

_last_call = 0.0


def _throttle():
    global _last_call
    wait = REQUEST_SPACING_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _get_with_retries(url, retries=5, backoff_s=20.0, **kwargs):
    """archive.org (CDX + snapshot fetch) rate-limits by refusing connections
    for ~a minute after a burst, and is occasionally just flaky (read
    timeouts). That is ordinary throttling, not a signal to give up or fake a
    result. Retry with generous backoff (20/40/60/80s) before letting the
    error propagate — total worst case ~3.5 min per URL."""
    last_exc = None
    for attempt in range(retries):
        try:
            return requests.get(url, headers=UA, **kwargs)
        except requests.exceptions.RequestException as e:
            last_exc = e
            print(
                f"  (retry {attempt + 1}/{retries} after {e.__class__.__name__}: {e})"
            )
            time.sleep(backoff_s * (attempt + 1))
    raise last_exc


def upsert(conn, source, season, kind, url, payload):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO raw.backtest_sources (source, season, kind, url, payload, fetched_at)
               VALUES (%s,%s,%s,%s,%s, now())
               ON CONFLICT (source, season, kind) DO UPDATE SET
                 url = EXCLUDED.url, payload = EXCLUDED.payload, fetched_at = now()""",
            (source, season, kind, url, json.dumps(payload)),
        )
    conn.commit()


# ---------- Step 1: dynastyprocess weekly FP ECR archive (kind='ecr') ----------


def fetch_dynastyprocess_ecr(conn, seasons=SEASONS):
    """Download db_fpecr.parquet once, filter to the superflex/2QB overall
    redraft cheatsheet page, and — per season — take the snapshot closest to
    Aug 20 (the brief's preseason proxy). Returns a dict season -> status info
    for the research doc."""
    print(f"[dynastyprocess] GET {DYNASTYPROCESS_PARQUET_URL}")
    _throttle()
    resp = requests.get(DYNASTYPROCESS_PARQUET_URL, headers=UA, timeout=180)
    resp.raise_for_status()
    df = pl.read_parquet(io.BytesIO(resp.content))
    op = df.filter(pl.col("fp_page") == DYNASTYPROCESS_FP_PAGE)
    if op.is_empty():
        raise SystemExit(
            f"[dynastyprocess] fp_page {DYNASTYPROCESS_FP_PAGE!r} not present in "
            "db_fpecr.parquet — schema/page slug changed upstream. Refusing to "
            "silently fall through; fix the page slug before re-running."
        )

    results = {}
    for season in seasons:
        target = date(season, 8, 20)
        season_dates = sorted(
            d for d in op["scrape_date"].unique().to_list() if d.startswith(str(season))
        )
        if not season_dates:
            print(
                f"[dynastyprocess] {season}: no snapshot in that calendar year — SKIP"
            )
            results[season] = {"status": "MISSING", "n": 0}
            continue
        closest = min(
            season_dates,
            key=lambda d: abs((date.fromisoformat(d) - target).days),
        )
        sub = op.filter(pl.col("scrape_date") == closest)
        rows = [
            {
                "name": r["player"],
                "position": r["pos"],
                "team": r["team"],
                "ecr": r["ecr"],
                "fp_id": r["id"],
            }
            for r in sub.iter_rows(named=True)
            if r["player"] and r["pos"]
        ]
        n = len(rows)
        dist = abs((date.fromisoformat(closest) - target).days)
        print(
            f"[dynastyprocess] {season}: closest snapshot {closest} ({dist}d from Aug 20), {n} rows"
        )
        if n < MIN_ADP_ROWS:
            print(
                f"[dynastyprocess] {season}: {n} < {MIN_ADP_ROWS} — NOT storing (documented failure)"
            )
            results[season] = {"status": "FAIL", "date": closest, "n": n}
            continue
        url = f"{DYNASTYPROCESS_PARQUET_URL}?scrape_date={closest}&fp_page=ppr-superflex-cheatsheets"
        upsert(conn, "dynastyprocess", season, "ecr", url, rows)
        results[season] = {
            "status": "PASS",
            "date": closest,
            "n": n,
            "sample": rows[:3],
            "url": url,
        }
    return results


# ---------- Step 3: Wayback FantasyPros projections (kind='projections') ----------


def find_closest_snapshot(url, season):
    """CDX search for snapshots of `url` within an Aug1-Sep20 window of
    `season` (the preseason proxy window used throughout this script).
    Returns (timestamp, snapshot_url, distance_days) or None."""
    _throttle()
    resp = _get_with_retries(
        "https://web.archive.org/cdx/search/cdx",
        params={
            "url": url,
            "from": f"{season}0801",
            "to": f"{season}0920",
            "output": "json",
            "filter": "statuscode:200",
            "limit": 30,
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    if len(rows) <= 1:
        return None
    target = date(season, 8, 20)
    candidates = []
    for ts, original in [(r[1], r[2]) for r in rows[1:]]:
        d = date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
        candidates.append((abs((d - target).days), ts, original))
    candidates.sort(key=lambda c: c[0])
    dist, ts, original = candidates[0]
    return ts, f"https://web.archive.org/web/{ts}/{original}", dist


def parse_projection_table(html, position, flex=False):
    """Parse a FantasyPros /nfl/projections/{pos}.php?week=draft data table
    (server-rendered HTML, table id='data'). Raises ValueError on any
    structural surprise rather than returning a partial/garbage parse.

    flex=True handles the /nfl/projections/flex.php layout, which inserts a
    POS column (e.g. 'WR1') between the player cell and the stat cells; each
    row's position comes from that cell instead of the page position."""
    m = re.search(r'<table[^>]*id="data"[^>]*>.*?</table>', html, re.S)
    if not m:
        raise ValueError(f"no id='data' table found for position={position}")
    table = m.group(0)

    thead_m = re.search(r"<thead>(.*?)</thead>", table, re.S)
    if not thead_m:
        raise ValueError(f"no thead found for position={position}")
    thead_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", thead_m.group(1), re.S)
    _skip_labels = {"player", "pos"} if flex else {"player"}
    if len(thead_rows) >= 2:
        group_cells = re.findall(
            r'<td[^>]*colspan="(\d+)"[^>]*>.*?<b>(.*?)</b>', thead_rows[0], re.S
        )
        groups = []
        for span, label in group_cells:
            groups.extend([label.strip()] * int(span))
        sub_cells = re.findall(
            r"<th[^>]*>(?:<small>)?(.*?)(?:</small>)?</th>", thead_rows[1], re.S
        )
        sub_labels = [
            s.strip() for s in sub_cells if s.strip().lower() not in _skip_labels
        ]
        if len(groups) != len(sub_labels):
            raise ValueError(
                f"header mismatch for {position}: {len(groups)} group cells vs "
                f"{len(sub_labels)} sub-labels — table layout changed upstream"
            )
        col_keys = [f"{g}_{s}" for g, s in zip(groups, sub_labels)]
    else:
        cells = re.findall(
            r"<th[^>]*>(?:<small>)?(.*?)(?:</small>)?</th>", thead_rows[0], re.S
        )
        col_keys = [c.strip() for c in cells if c.strip().lower() not in _skip_labels]

    tbody_m = re.search(r"<tbody>(.*?)</tbody>", table, re.S)
    if not tbody_m:
        raise ValueError(f"no tbody found for position={position}")
    row_htmls = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody_m.group(1), re.S)

    def clean(cell):
        txt = re.sub("<[^>]+>", "", cell).replace(",", "").strip()
        try:
            return float(txt)
        except ValueError:
            return txt or None

    players = []
    skipped_positions = []
    for row in row_htmls:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) < 2:
            continue
        name_cell = tds[0]
        name_m = re.search(
            r'<a\s+[^>]*class="([^"]*player-name[^"]*)"[^>]*>([^<]+)</a>', name_cell
        )
        if not name_m:
            continue
        name = name_m.group(2).strip()
        after = name_cell[name_m.end() :]
        team_m = re.match(r"\s*([A-Z]{2,3})\b", after)
        team = team_m.group(1) if team_m else None
        fpid_m = re.search(r"fp-id-(\d+)", name_cell)
        fp_id = fpid_m.group(1) if fpid_m else None

        if flex:
            if len(tds) < 3:
                continue
            pos_txt = re.sub("<[^>]+>", "", tds[1]).strip()
            row_position = re.sub(r"\d+$", "", pos_txt).upper()
            if row_position not in {"QB", "RB", "WR", "TE", "K", "DST"}:
                # FP's flex page occasionally lists a fringe gadget player
                # under an IDP position (observed 2025: 1 DT, 1 LB). Skip
                # those rows but track them — a flood of unrecognized
                # positions means the layout changed and we must fail loud
                # (checked after the loop).
                skipped_positions.append(pos_txt)
                continue
            stat_cells = tds[2:]
        else:
            row_position = position.upper()
            stat_cells = tds[1:]
        if not stat_cells:
            continue
        last_cell = stat_cells[-1]
        sort_m = re.search(r'data-sort-value="([\d.\-]+)"', last_cell)
        fpts = float(sort_m.group(1)) if sort_m else clean(last_cell)

        stats = {}
        if len(stat_cells) == len(col_keys):
            stats = {k: clean(c) for k, c in zip(col_keys, stat_cells)}

        players.append(
            {
                "name": name,
                "position": row_position,
                "team": team,
                "fp_id": fp_id,
                "fpts": fpts,
                "stats": stats,
            }
        )

    if len(players) < 5:
        raise ValueError(
            f"parsed only {len(players)} players for position={position} — "
            "table structure likely changed or page is not the projections table; refusing to store"
        )
    if len(skipped_positions) > 0.02 * (len(players) + len(skipped_positions)):
        raise ValueError(
            f"{len(skipped_positions)} rows with unrecognized POS "
            f"({skipped_positions[:8]}...) out of {len(players) + len(skipped_positions)} — "
            "that is not a couple of fringe misclassifications, the layout changed; refusing to store"
        )
    if skipped_positions:
        print(
            f"  (skipped {len(skipped_positions)} fringe non-flex rows: {skipped_positions})"
        )

    # Season-vs-weekly guard: FantasyPros serves the SAME table layout for
    # weekly projections (the page without ?week=draft, or an in-season
    # default view). Weekly FPTS top out around ~25; season projections for
    # every fantasy-relevant position top ~100+. Verified empirically
    # 2026-07-10: weekly snapshots parsed at 8-17 FPTS. Reject rather than
    # store a weekly table masquerading as a season one.
    top_fpts = sorted(
        (p["fpts"] for p in players if isinstance(p["fpts"], float)), reverse=True
    )[:5]
    if not top_fpts or max(top_fpts) < 60:
        raise ValueError(
            f"top FPTS {top_fpts} for position={position} looks like WEEKLY "
            "projections, not season totals — refusing to store"
        )
    return players


def find_flex_snapshot(season):
    """CDX prefix search on /nfl/projections/flex.php (query strings vary) for
    week=draft snapshots in the Aug1-Sep20 window, preferring scoring=PPR
    (matches the ECR page and league scoring family). Returns
    (timestamp, snapshot_url, distance_days) or None."""
    _throttle()
    resp = _get_with_retries(
        "https://web.archive.org/cdx/search/cdx",
        params={
            "url": "https://www.fantasypros.com/nfl/projections/flex.php",
            "matchType": "prefix",
            "from": f"{season}0801",
            "to": f"{season}0920",
            "output": "json",
            "filter": "statuscode:200",
            "limit": 50,
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    if len(rows) <= 1:
        return None
    target = date(season, 8, 20)
    candidates = []
    for ts, original in [(r[1], r[2]) for r in rows[1:]]:
        if "week=draft" not in original:
            continue  # non-draft view = weekly projections, useless here
        d = date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
        is_ppr = "scoring=PPR" in original
        candidates.append((0 if is_ppr else 1, abs((d - target).days), ts, original))
    if not candidates:
        return None
    candidates.sort()
    _, dist, ts, original = candidates[0]
    return ts, f"https://web.archive.org/web/{ts}/{original}", dist


def fetch_wayback_projections(conn, seasons=SEASONS):
    """For each season, hit the Wayback CDX index for each position's
    /nfl/projections/{pos}.php?week=draft page within the Aug1-Sep20 window,
    fetch+parse whichever snapshots exist, and store the union (whatever
    positions were actually found) as one 'projections' row per season.
    If any of rb/wr/te are missing, also try the flex.php?week=draft page
    (one table covering RB+WR+TE). Duplicates (player appearing on both a
    positional page and flex) are dropped, positional page wins."""
    results = {}
    for season in seasons:
        season_players = []
        position_report = {}
        primary_url = None

        def _ingest(players, snapshot_url, ts):
            nonlocal primary_url
            seen = {(p["name"], p["position"]) for p in season_players}
            added = 0
            for p in players:
                if (p["name"], p["position"]) in seen:
                    continue
                p["snapshot_url"] = snapshot_url
                p["snapshot_date"] = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
                season_players.append(p)
                added += 1
            if primary_url is None:
                primary_url = snapshot_url
            return added

        for pos in PROJECTION_POSITIONS:
            page_url = FP_PROJECTIONS_URL.format(pos=pos)
            found = find_closest_snapshot(page_url, season)
            if not found:
                print(
                    f"[wayback_fp] {season} {pos}: no snapshot in Aug1-Sep20 window — MISSING"
                )
                position_report[pos] = {"status": "MISSING"}
                continue
            ts, snapshot_url, dist = found
            print(
                f"[wayback_fp] {season} {pos}: snapshot {ts} ({dist}d from Aug 20) — fetching"
            )
            _throttle()
            resp = _get_with_retries(snapshot_url, timeout=45)
            resp.raise_for_status()
            try:
                players = parse_projection_table(resp.text, pos)
            except ValueError as e:
                print(f"[wayback_fp] {season} {pos}: PARSE FAILED ({e}) — not stored")
                position_report[pos] = {
                    "status": "PARSE_FAILED",
                    "snapshot": snapshot_url,
                }
                continue
            n_added = _ingest(players, snapshot_url, ts)
            position_report[pos] = {
                "status": "OK",
                "n": n_added,
                "snapshot": snapshot_url,
                "distance_days": dist,
            }

        missing_flexable = [
            p for p in ("rb", "wr", "te") if position_report[p]["status"] != "OK"
        ]
        if missing_flexable:
            print(
                f"[wayback_fp] {season}: {'/'.join(missing_flexable)} missing — trying flex.php?week=draft"
            )
            found = find_flex_snapshot(season)
            if not found:
                print(
                    f"[wayback_fp] {season} flex: no week=draft snapshot in window — MISSING"
                )
                position_report["flex"] = {"status": "MISSING"}
            else:
                ts, snapshot_url, dist = found
                print(
                    f"[wayback_fp] {season} flex: snapshot {ts} ({dist}d from Aug 20) — fetching"
                )
                _throttle()
                resp = _get_with_retries(snapshot_url, timeout=45)
                resp.raise_for_status()
                try:
                    players = parse_projection_table(resp.text, "flex", flex=True)
                except ValueError as e:
                    print(
                        f"[wayback_fp] {season} flex: PARSE FAILED ({e}) — not stored"
                    )
                    position_report["flex"] = {
                        "status": "PARSE_FAILED",
                        "snapshot": snapshot_url,
                    }
                    players = None
                if players:
                    n_added = _ingest(players, snapshot_url, ts)
                    position_report["flex"] = {
                        "status": "OK",
                        "n": n_added,
                        "snapshot": snapshot_url,
                        "distance_days": dist,
                    }

        n = len(season_players)
        covered = {p["position"] for p in season_players}
        core_ok = {"QB", "RB", "WR", "TE"} <= covered
        # PASS needs BOTH the row-count bar and full core-position coverage —
        # 250 QB+TE rows is not a usable draft-projection pool.
        if n >= MIN_PROJ_ROWS and core_ok:
            status = "PASS"
        elif n > 0:
            status = "PARTIAL"
        else:
            status = "FAIL"
        print(
            f"[wayback_fp] {season}: total {n} players, positions {sorted(covered)} — {status}"
        )
        if n > 0:
            upsert(
                conn,
                "wayback_fp",
                season,
                "projections",
                primary_url,
                season_players,
            )
        results[season] = {
            "status": status,
            "n": n,
            "covered": sorted(covered),
            "positions": position_report,
            "sample": season_players[:3],
        }
    return results


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=SEASONS,
        help="subset of seasons to (re)source, e.g. --seasons 2025 (default: all)",
    )
    args = ap.parse_args()
    seasons = args.seasons

    conn = connect()

    print("=" * 70)
    print("Step 1: dynastyprocess weekly FP ECR archive (superflex/2QB, kind=ecr)")
    print("=" * 70)
    ecr_results = fetch_dynastyprocess_ecr(conn, seasons)

    print()
    print("=" * 70)
    print("Step 2: ffverse / dynastyprocess GitHub orgs for archived projection CSVs")
    print("=" * 70)
    print(
        "[ffverse] Probed github.com/ffverse/* and github.com/dynastyprocess/data — "
        "no archived preseason stat-line/projection CSVs found (ffanalytics is a "
        "live-scrape R package with no historical archive; dynastyprocess/data only "
        "archives ECR/rank pages, not projected stat lines). See research doc — "
        "documented miss, nothing to fetch/store for this step."
    )

    print()
    print("=" * 70)
    print("Step 3: Wayback Machine FantasyPros projections (kind=projections)")
    print("=" * 70)
    proj_results = fetch_wayback_projections(conn, seasons)

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    for season in seasons:
        e = ecr_results.get(season, {})
        p = proj_results.get(season, {})
        print(
            f"{season}: ecr={e.get('status')} (n={e.get('n')})  "
            f"projections={p.get('status')} (n={p.get('n')})"
        )

    with open("/tmp/backtest_sourcing_results.json", "w") as f:
        json.dump(
            {"ecr": ecr_results, "projections": proj_results}, f, indent=2, default=str
        )
    print(
        "\nFull result dump written to /tmp/backtest_sourcing_results.json (doc-writing aid)."
    )


if __name__ == "__main__":
    main()
