import asyncio
import unittest
from unittest.mock import patch

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
    def _active_access(self, package_id):
        return patch.multiple(
            db,
            has_active_access=lambda _uid: True,
            get_access=lambda _uid: {"package_id": package_id},
        )

    def test_binary_modes_are_classified(self):
        with self._active_access("admin_binary_nonmtg_90d"):
            self.assertTrue(db.has_binary_access(1))
            self.assertFalse(db.has_forex_access(1))
            self.assertEqual(db.get_binary_access_mode(1), "nonmtg")
        with self._active_access("admin_binary_mtg_3m"):
            self.assertTrue(db.has_binary_access(1))
            self.assertEqual(db.get_binary_access_mode(1), "mtg")

    def test_forex_grant_unlocks_forex_not_binary(self):
        with self._active_access("admin_forex_life"):
            self.assertTrue(db.has_forex_access(1))
            self.assertFalse(db.has_binary_access(1))

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