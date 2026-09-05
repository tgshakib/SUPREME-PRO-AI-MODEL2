import os
import unittest
from unittest.mock import AsyncMock, patch

from keyboards import main_menu_kb
from middleware import FutureSignalRelayMiddleware


class _Event:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class _Response:
    status_code = 200


class _Client:
    def __init__(self, post):
        self.post = post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FutureSignalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_home_contains_future_signal_button(self):
        buttons = [
            button
            for row in main_menu_kb().inline_keyboard
            for button in row
        ]
        match = [
            button for button in buttons
            if button.text == "🔮 FUTURE SIGNAL • TG"
        ]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0].callback_data, "tgadv:futuresignal")

    async def test_relay_forwards_the_untouched_update(self):
        payload = {
            "update_id": 42,
            "callback_query": {
                "id": "callback-id",
                "data": "tgadv:futuresignal",
            },
        }
        post = AsyncMock(return_value=_Response())
        handler = AsyncMock(return_value="handled")
        middleware = FutureSignalRelayMiddleware()

        with (
            patch.dict(os.environ, {"SESSION_SECRET": "relay-test-secret"}),
            patch(
                "middleware.httpx.AsyncClient",
                return_value=_Client(post),
            ),
        ):
            result = await middleware(handler, _Event(payload), {})

        self.assertEqual(result, "handled")
        post.assert_awaited_once()
        self.assertEqual(post.await_args.kwargs["json"], payload)
        self.assertEqual(
            post.await_args.kwargs["headers"]["x-future-relay-secret"],
            "relay-test-secret",
        )
        handler.assert_awaited_once()

    async def test_main_bot_continues_if_relay_is_offline(self):
        post = AsyncMock(side_effect=OSError("relay offline"))
        handler = AsyncMock(return_value="handled")
        middleware = FutureSignalRelayMiddleware()

        with (
            patch.dict(os.environ, {"SESSION_SECRET": "relay-test-secret"}),
            patch(
                "middleware.httpx.AsyncClient",
                return_value=_Client(post),
            ),
        ):
            result = await middleware(
                handler,
                _Event({"update_id": 43}),
                {},
            )

        self.assertEqual(result, "handled")
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()