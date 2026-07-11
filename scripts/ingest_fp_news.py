#!/usr/bin/env python3
"""FantasyPros /news ingest -> signals.signals (typed, dedup by link).

Deliberate deviation from design §4.7 (pinned in Task 14 brief): no LLM
digestion here. FP news items arrive pre-structured (impact, player_id,
categories), so a deterministic keyword mapping replaces the agent-digestion
stage. Direction/magnitude of any resulting scoring adjustment are set by the
HUMAN at the Task 15 confirm gate -- strictly more conservative than the
capped-agent path in the original design.

Live-verified payload shape (2026-07-10, public/free tier):
    {"items": [{"id", "created", "author", "player_id", "team_id", "title",
                "sport_id", "categories", "link", "desc", "impact"}, ...],
     "count", "limit", "public_api_limited", "tier", ...}
The free tier caps `items` well below the requested `limit` (observed: asked
for 5, API returned 3, count=3, public_api_limited=True). That's expected,
not an error.

Category -> signal_type mapping observed live: the sample only ever produced
["Commentary", "News"], which both fall through to the `news` bucket. The
Injury / Depth Chart / Breakout / Sleeper branches below are pinned per the
design spec but have not yet been exercised by a live payload -- watch for
divergence and reconcile this dict + the test fixture if FP's real vocabulary
differs once one of those categories is actually observed.
"""
import argparse
import json

from ffi.db import connect
from ffi.ingest.base import IngestError
from ffi.ingest.fantasypros import FpBudgetExceededError, FpClient, fp_calls_today

SOURCE = "fp_news"
NEWS_LIMIT = 5
# Leave 2 calls of spare headroom under FpClient's own 30/day budget so a
# late-day human probe/retry never gets blocked by the morning ingest chain.
HEADROOM_CEILING = 28

CATEGORY_RULES = (
    ("injury", "injury"),
    ("depth chart", "depth_chart"),
    ("breakout", "hype"),
    ("sleeper", "hype"),
)


def map_signal_type(categories) -> str:
    cats_lower = [c.lower() for c in (categories or [])]
    for keyword, signal_type in CATEGORY_RULES:
        if any(keyword in c for c in cats_lower):
            return signal_type
    return "news"


def _resolve_xwalk(conn, fp_player_id):
    """fp player_id -> (xwalk_id, name), both None when unresolved (no id on
    the item, or no crosswalk row matches it)."""
    if fp_player_id is None:
        return None, None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT xwalk_id, name FROM public.player_id_xwalk WHERE fantasypros_id = %s",
            (str(fp_player_id),),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def ingest_items(conn, items: list[dict]) -> dict:
    """Map + store FP news items into signals.signals. Fail-loud on malformed
    items (missing title/link -- a schema change in FP's payload must be
    loud, ADR D6), never silently dropped. Unmatched-player rows are kept
    with xwalk_id NULL and counted, never dropped."""
    stored = 0
    unmatched = 0
    for item in items:
        title = item.get("title")
        link = item.get("link")
        if not title or not link:
            raise IngestError(f"FP news item missing title/link: {item!r}")
        signal_type = map_signal_type(item.get("categories"))
        xwalk_id, player_name = _resolve_xwalk(conn, item.get("player_id"))
        if xwalk_id is None:
            unmatched += 1
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO signals.signals
                   (source, external_id, xwalk_id, player_name, signal_type,
                    title, summary, impact, evidence_url, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source, external_id) DO NOTHING""",
                (
                    SOURCE,
                    link,
                    xwalk_id,
                    player_name,
                    signal_type,
                    title,
                    item.get("desc"),
                    item.get("impact"),
                    link,
                    json.dumps(item),
                ),
            )
            if cur.rowcount:
                stored += 1
    return {"seen": len(items), "stored": stored, "unmatched": unmatched}


def run_daily(conn, limit: int = NEWS_LIMIT) -> dict:
    used = fp_calls_today(conn)
    if used + 1 > HEADROOM_CEILING:
        raise FpBudgetExceededError(
            f"FP news headroom: {used}/{HEADROOM_CEILING} (of {30} total) already "
            "used today -- refusing this call to leave spare budget. Re-run tomorrow."
        )
    client = FpClient(conn)
    payload = client.get("news", {"limit": limit})
    items = payload.get("items")
    if items is None:
        raise IngestError(
            f"FP /news payload missing 'items' key -- payload keys: {sorted(payload)}"
        )
    result = ingest_items(conn, items)
    conn.commit()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    args = ap.parse_args()
    if not args.daily:
        ap.error("choose --daily")
    conn = connect()
    result = run_daily(conn)
    print(
        f"fp_news: seen={result['seen']} stored={result['stored']} "
        f"unmatched={result['unmatched']} (calls today: {fp_calls_today(conn)}/30)"
    )


if __name__ == "__main__":
    main()
