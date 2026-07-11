"""Task 14: FP /news ingest -> signals.signals.

Fixture item shapes are pinned from the live payload observed in Step 3
(2026-07-10): top-level key is `items` (not `news`); each item has
{id, created, author, player_id, team_id, title, sport_id, categories,
link, desc, impact}. The live sample only ever showed categories
["Commentary", "News"] (all fell to the `news` bucket), so this fixture
adds synthetic Injury / Depth Chart / Breakout items to exercise the
other mapping branches, which have not yet been observed live.
No HTTP in these tests: FpClient._http_get is monkeypatched.
"""
import pytest

from ffi.ingest.fantasypros import FpBudgetExceededError
from ingest_fp_news import ingest_items, map_signal_type, run_daily

FIXTURE_ITEMS = [
    {
        "id": 595329,
        "created": "2026-07-06 21:17:42",
        "author": "Ari Koslow",
        "player_id": 26133,
        "team_id": "FA",
        "title": "Terrion Arnold clears waivers, expected to draw interest",
        "sport_id": "NFL",
        "categories": ["Commentary", "News"],
        "link": "https://www.fantasypros.com/nfl/news/595329/terrion-arnold.php",
        "desc": "Terrion Arnold cleared waivers on Monday and is now a free agent.",
        "impact": "Arnold continues to face legal issues in Florida.",
    },
    {
        "id": 600001,
        "created": "2026-07-08 10:00:00",
        "author": "Test Author",
        "player_id": 99999,
        "team_id": "KC",
        "title": "Star RB leaves practice with hamstring tightness",
        "sport_id": "NFL",
        "categories": ["Injury", "News"],
        "link": "https://www.fantasypros.com/nfl/news/600001/star-rb-injury.php",
        "desc": "Left practice early Tuesday.",
        "impact": "Considered day-to-day; monitor Wednesday's estimation.",
    },
    {
        "id": 600002,
        "created": "2026-07-08 11:00:00",
        "author": "Test Author",
        "player_id": None,
        "team_id": "DEN",
        "title": "Broncos shuffle backup WR depth chart",
        "sport_id": "NFL",
        "categories": ["Depth Chart", "News"],
        "link": "https://www.fantasypros.com/nfl/news/600002/broncos-depth-chart.php",
        "desc": "Coaching staff comments on WR3 competition.",
        "impact": "Muddies waiver value of the WR3 battle.",
    },
]


def _seed_xwalk(db, fantasypros_id, name):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, fantasypros_id) "
            "VALUES (%s, %s) RETURNING xwalk_id",
            (name, str(fantasypros_id)),
        )
        return cur.fetchone()[0]


class TestMapSignalType:
    def test_injury_category_maps_to_injury(self):
        assert map_signal_type(["Injury", "News"]) == "injury"

    def test_depth_chart_category_maps_to_depth_chart(self):
        assert map_signal_type(["Depth Chart"]) == "depth_chart"

    def test_breakout_category_maps_to_hype(self):
        assert map_signal_type(["Breakout Candidate"]) == "hype"

    def test_sleeper_category_maps_to_hype(self):
        assert map_signal_type(["Sleeper Pick"]) == "hype"

    def test_commentary_news_falls_through_to_news(self):
        assert map_signal_type(["Commentary", "News"]) == "news"

    def test_empty_categories_falls_through_to_news(self):
        assert map_signal_type([]) == "news"
        assert map_signal_type(None) == "news"


class TestIngestItems:
    def test_maps_types_and_resolves_xwalk(self, db):
        xwalk_id = _seed_xwalk(db, 99999, "Star Runningback")
        result = ingest_items(db, FIXTURE_ITEMS)
        db.commit()

        with db.cursor() as cur:
            cur.execute(
                "SELECT external_id, xwalk_id, player_name, signal_type, title, "
                "summary, impact, evidence_url, status FROM signals.signals "
                "ORDER BY external_id"
            )
            rows = {r[0]: r for r in cur.fetchall()}

        arnold_link = "https://www.fantasypros.com/nfl/news/595329/terrion-arnold.php"
        rb_link = "https://www.fantasypros.com/nfl/news/600001/star-rb-injury.php"
        depth_link = (
            "https://www.fantasypros.com/nfl/news/600002/broncos-depth-chart.php"
        )

        assert rows[arnold_link][3] == "news"
        assert rows[arnold_link][1] is None  # unmatched, not seeded

        assert rows[rb_link][3] == "injury"
        assert rows[rb_link][1] == xwalk_id
        assert rows[rb_link][2] == "Star Runningback"

        assert rows[depth_link][3] == "depth_chart"
        assert rows[depth_link][1] is None  # no player_id on this item
        assert rows[depth_link][8] == "pending"

        assert result == {"seen": 3, "stored": 3, "unmatched": 2}

    def test_second_run_dedupes_via_link(self, db):
        _seed_xwalk(db, 99999, "Star Runningback")
        ingest_items(db, FIXTURE_ITEMS)
        db.commit()

        result = ingest_items(db, FIXTURE_ITEMS)
        db.commit()

        assert result["stored"] == 0
        assert result["seen"] == 3
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM signals.signals")
            assert cur.fetchone()[0] == 3

    def test_missing_title_raises(self, db):
        bad_item = dict(FIXTURE_ITEMS[0])
        bad_item["title"] = None
        with pytest.raises(Exception):
            ingest_items(db, [bad_item])

    def test_missing_link_raises(self, db):
        bad_item = dict(FIXTURE_ITEMS[0])
        del bad_item["link"]
        with pytest.raises(Exception):
            ingest_items(db, [bad_item])


class TestRunDailyBudgetGuard:
    def test_aborts_before_any_http_when_headroom_exhausted(self, db, monkeypatch):
        def _boom_get(self, endpoint, params, season=None):
            raise AssertionError("must not call the FP API when headroom is exhausted")

        monkeypatch.setattr("ingest_fp_news.fp_calls_today", lambda conn: 28)
        monkeypatch.setattr("ffi.ingest.fantasypros.FpClient.get", _boom_get)

        with pytest.raises(FpBudgetExceededError):
            run_daily(db)

    def test_proceeds_when_headroom_available(self, db, monkeypatch):
        monkeypatch.setattr("ingest_fp_news.fp_calls_today", lambda conn: 8)
        monkeypatch.setattr(
            "ffi.ingest.fantasypros.FpClient.get",
            lambda self, endpoint, params, season=None: {"items": FIXTURE_ITEMS[:1]},
        )
        result = run_daily(db)
        assert result["seen"] == 1
        assert result["stored"] == 1
