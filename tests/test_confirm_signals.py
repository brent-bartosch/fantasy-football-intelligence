"""Task 15: human confirm gate (scripts/confirm_signals.py) -- the only place
a pending signal can flip to confirmed/denied, and the only place a nonzero
pct can be requested for one."""
import json

import pytest

from confirm_signals import confirm_signal, deny_signal, list_pending
from ffi.signals_apply import AdjustmentCapError


def _seed_xwalk(db, name, fantasypros_id):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, fantasypros_id) VALUES (%s, %s) "
            "RETURNING xwalk_id",
            (name, str(fantasypros_id)),
        )
        return cur.fetchone()[0]


def _seed_signal(
    db, xwalk_id=None, player_name=None, status="pending", title="t", impact="hi"
):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO signals.signals
               (source, external_id, xwalk_id, player_name, signal_type, title, impact,
                evidence_url, payload, status)
               VALUES ('fp_news', %s, %s, %s, 'news', %s, %s, 'http://x', %s, %s)
               RETURNING signal_id""",
            (
                f"ext-{title}-{xwalk_id}",
                xwalk_id,
                player_name,
                title,
                impact,
                json.dumps({}),
                status,
            ),
        )
        return cur.fetchone()[0]


def _signal_row(db, signal_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, decided_at FROM signals.signals WHERE signal_id=%s",
            (signal_id,),
        )
        return cur.fetchone()


class TestListPending:
    def test_lists_only_pending_with_expected_shape(self, db):
        xwalk_id = _seed_xwalk(db, "Matched Player", 1)
        matched = _seed_signal(
            db, xwalk_id=xwalk_id, player_name="Matched Player", title="matched"
        )
        unmatched = _seed_signal(db, xwalk_id=None, title="unmatched")
        _seed_signal(db, xwalk_id=xwalk_id, status="confirmed", title="already done")
        db.commit()

        pending = list_pending(db)
        ids = {s["signal_id"] for s in pending}
        assert ids == {matched, unmatched}

        by_id = {s["signal_id"]: s for s in pending}
        assert by_id[matched]["player"] == "Matched Player"
        assert by_id[unmatched]["player"] == "UNMATCHED"


class TestDenySignal:
    def test_sets_status_denied_and_decided_at(self, db):
        signal_id = _seed_signal(db)
        db.commit()

        deny_signal(db, signal_id)

        status, decided_at = _signal_row(db, signal_id)
        assert status == "denied"
        assert decided_at is not None


class TestConfirmSignal:
    def test_informational_confirm_sets_status_no_adjustment(self, db):
        signal_id = _seed_signal(db)
        db.commit()

        result = confirm_signal(db, signal_id, 0.0)

        assert result is None
        status, decided_at = _signal_row(db, signal_id)
        assert status == "confirmed"
        assert decided_at is not None
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM signals.adjustments")
            assert cur.fetchone()[0] == 0

    def test_informational_confirm_on_nonexistent_id_raises(self, db):
        """Regression: the UPDATE previously matched zero rows silently and
        returned None, indistinguishable from a real informational confirm
        -- a fat-fingered signal_id looked like it worked."""
        with pytest.raises(ValueError, match="999999.*does not exist"):
            confirm_signal(db, 999999, 0.0)

    def test_informational_confirm_on_already_confirmed_id_raises(self, db):
        signal_id = _seed_signal(db, status="confirmed")
        db.commit()

        with pytest.raises(ValueError, match="does not exist or is not pending"):
            confirm_signal(db, signal_id, 0.0)

    def test_confirm_with_pct_on_already_denied_id_raises(self, db):
        xwalk_id = _seed_xwalk(db, "Player Z", 99)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id, status="denied")
        db.commit()

        with pytest.raises(ValueError, match="does not exist or is not pending"):
            confirm_signal(db, signal_id, 0.05)

    def test_confirm_with_pct_applies_adjustment(self, db):
        xwalk_id = _seed_xwalk(db, "Player A", 10)
        signal_id = _seed_signal(db, xwalk_id=xwalk_id)
        db.commit()

        adjustment_id = confirm_signal(db, signal_id, 0.05, note="hype")

        assert isinstance(adjustment_id, int)
        status, _ = _signal_row(db, signal_id)
        assert status == "confirmed"
        with db.cursor() as cur:
            cur.execute(
                "SELECT xwalk_id, pct, note FROM signals.adjustments WHERE adjustment_id=%s",
                (adjustment_id,),
            )
            assert cur.fetchone() == (xwalk_id, 0.05, "hype")

    def test_nonzero_pct_on_unmatched_signal_refuses_and_leaves_pending(self, db):
        signal_id = _seed_signal(db, xwalk_id=None)
        db.commit()

        with pytest.raises(AdjustmentCapError, match="no resolved player"):
            confirm_signal(db, signal_id, 0.05)

        status, decided_at = _signal_row(db, signal_id)
        assert status == "pending"
        assert decided_at is None

    def test_cap_violation_rolls_back_status_flip(self, db):
        """A pct within confirm_signal's own per-signal check but rejected by
        apply_adjustment's per-day cap (a second adjustment for the same
        player, same day) must leave the SECOND signal 'pending', not
        'confirmed' with no adjustment -- no partial writes at the CLI layer
        either."""
        xwalk_id = _seed_xwalk(db, "Player B", 11)
        first_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="first")
        second_signal_id = _seed_signal(db, xwalk_id=xwalk_id, title="second")
        db.commit()

        confirm_signal(db, first_signal_id, 0.05)

        with pytest.raises(AdjustmentCapError):
            confirm_signal(db, second_signal_id, 0.05)

        status, decided_at = _signal_row(db, second_signal_id)
        assert status == "pending"
        assert decided_at is None
