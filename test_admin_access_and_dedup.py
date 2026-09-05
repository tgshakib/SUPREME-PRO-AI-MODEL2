import asyncio
import os
import sqlite3
import tempfile
import unittest

import database as db
from keyboards import (
    add_user_binary_mode_kb,
    add_user_duration_kb,
    add_user_product_kb,
)
from middleware import (
    AntiSpamMiddleware,
    _admission_locks,
    _last_click,
    _seen_cb_ids,
    _user_locks,
)


class AccessClassificationTests(unittest.TestCase):
    def setUp(self):
        self.old_db_path = db.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        db.init_db()
        db.upsert_user(1, "member", "Test Member")

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_binary_modes_are_classified(self):
        db.grant_access(1, "temporary", 90, "admin_binary_nonmtg_90d")
        self.assertTrue(db.has_binary_access(1))
        self.assertFalse(db.has_forex_access(1))
        self.assertEqual(db.get_binary_access_mode(1), "nonmtg")
        db.grant_access(1, "temporary", 90, "admin_binary_mtg_3m")
        self.assertTrue(db.has_binary_access(1))
        self.assertEqual(db.get_binary_access_mode(1), "mtg")

    def test_existing_binary_card_variant_follows_access_mode(self):
        from signals import _legacy_binary_card

        db.grant_access(1, "temporary", 90, "admin_binary_nonmtg_90d")
        for market in ("LIVE", "PO OTC"):
            text, _ = _legacy_binary_card(
                "EUR/USD", market, "1 MIN", 1,
                "BUY", "📈 BULLISH", 95,
            )
            self.assertIn("<b>NON MARTINGALE</b>", text)
            self.assertNotIn("Community: @Traderguide_bot", text)

        db.grant_access(1, "temporary", 90, "admin_binary_mtg_90d")
        for market in ("LIVE", "PO OTC"):
            text, _ = _legacy_binary_card(
                "EUR/USD", market, "1 MIN", 1,
                "BUY", "📈 BULLISH", 95,
            )
            self.assertIn("<b>1 Step Required</b>", text)
            self.assertIn("Community: @Traderguide_bot", text)

    def test_forex_grant_unlocks_forex_not_binary(self):
        db.grant_access(1, "lifetime", 0, "admin_forex_life")
        self.assertTrue(db.has_forex_access(1))
        self.assertFalse(db.has_binary_access(1))

    def test_binary_and_forex_grants_coexist(self):
        db.grant_access(1, "temporary", 90, "admin_forex_90d")
        db.grant_access(1, "lifetime", 0, "admin_binary_nonmtg_life")
        self.assertTrue(db.has_forex_access(1))
        self.assertTrue(db.has_binary_access(1))
        self.assertEqual(db.get_binary_access_mode(1), "nonmtg")
        self.assertEqual(
            {row["scope"] for row in db.get_accesses(1)},
            {"binary", "forex"},
        )

    def test_paid_binary_and_forex_packages_coexist(self):
        db.grant_access(1, "temporary", 7, "mtg_7d", "Binary MTG")
        db.grant_access(1, "temporary", 30, "gz_30d", "Forex")
        self.assertTrue(db.has_binary_access(1))
        self.assertTrue(db.has_forex_access(1))
        self.assertEqual(len(db.get_accesses(1)), 2)

    def test_expiry_and_revocation_are_product_specific(self):
        from datetime import timedelta

        db.grant_access_delta(
            1, "temporary", timedelta(seconds=-1),
            "admin_binary_mtg_expired", "Binary MTG",
        )
        db.grant_access(1, "lifetime", 0, "admin_forex_life", "Forex")
        self.assertFalse(db.has_binary_access(1))
        self.assertTrue(db.has_forex_access(1))
        db.revoke_access(1, scope="binary")
        self.assertTrue(db.has_forex_access(1))
        self.assertEqual(
            {row["scope"] for row in db.get_accesses(1)},
            {"forex"},
        )

    def test_old_single_access_database_is_migrated(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        db.DB_PATH = legacy_path
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                "CREATE TABLE access ("
                "user_id INTEGER PRIMARY KEY, access_type TEXT NOT NULL, "
                "package_id TEXT, package_label TEXT, granted_at TEXT, "
                "expires_at TEXT)"
            )
            conn.execute(
                "INSERT INTO access VALUES(?,?,?,?,?,?)",
                (7, "lifetime", "nmg_life", "NON-MTG", "2026-01-01", None),
            )
        db.init_db()
        self.assertTrue(db.has_binary_access(7))
        self.assertEqual(db.get_binary_access_mode(7), "nonmtg")
        self.assertFalse(db.has_forex_access(7))

    def test_add_user_keyboards_expose_requested_steps(self):
        product_data = {
            button.callback_data
            for row in add_user_product_kb().inline_keyboard
            for button in row
        }
        mode_data = {
            button.callback_data
            for row in add_user_binary_mode_kb().inline_keyboard
            for button in row
        }
        duration_data = {
            button.callback_data
            for row in add_user_duration_kb().inline_keyboard
            for button in row
        }
        self.assertIn("adm:add:type:forex", product_data)
        self.assertIn("adm:add:type:binary", product_data)
        self.assertIn("adm:add:mode:mtg", mode_data)
        self.assertIn("adm:add:mode:nonmtg", mode_data)
        self.assertIn("adm:dur:custom", duration_data)
        self.assertIn("adm:dur:life", duration_data)


class _User:
    id = 987654


class _Callback:
    from_user = _User()

    def __init__(self, callback_id, callback_data):
        self.id = callback_id
        self.data = callback_data
        self.answers = 0

    async def answer(self, *args, **kwargs):
        self.answers += 1


class DuplicateTapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _seen_cb_ids.clear()
        _last_click.clear()
        _user_locks.clear()
        _admission_locks.clear()

    async def test_concurrent_buttons_do_not_queue_duplicate_panels(self):
        middleware = AntiSpamMiddleware()
        entered = asyncio.Event()
        release = asyncio.Event()
        handled = 0

        async def handler(_event, _data):
            nonlocal handled
            handled += 1
            entered.set()
            await release.wait()

        first = _Callback("callback-1", "m:home")
        second = _Callback("callback-2", "m:binary")
        first_task = asyncio.create_task(middleware(handler, first, {}))
        await entered.wait()
        await middleware(handler, second, {})
        release.set()
        await first_task

        self.assertEqual(handled, 1)
        self.assertEqual(second.answers, 1)


if __name__ == "__main__":
    unittest.main()