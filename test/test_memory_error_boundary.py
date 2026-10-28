"""Neutral regression cases: derived memory must not leak into bot dialogue."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from companion.app import App
from companion.pyramid_memory import MemoryProposalError
from companion.services import ServiceError


class ErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_failures_are_not_sent_verbatim(self):
        for text, error in [
            ('今天喝茶嗎？', ValueError('記憶人物或逐字證據無效')),
            ('/photo', MemoryProposalError('記憶人物或逐字證據無效')),
            ('今天喝茶嗎？', ServiceError('private internal diagnostic')),
        ]:
            with self.subTest(text=text, error=type(error).__name__):
                store=Mock()
                store.next_update.side_effect=[(1, {'text':text, 'chat':{'id':42}}), asyncio.CancelledError()]
                app=SimpleNamespace(store=store, inbox_event=asyncio.Event(), busy=False,
                                    handle=AsyncMock(side_effect=error), notify=AsyncMock())
                with self.assertRaises(asyncio.CancelledError):
                    await App.inbox_worker(app)
                app.notify.assert_awaited_once()
                notice=app.notify.await_args.args[1]
                self.assertNotIn(str(error),notice)
                store.finish_update.assert_called_once_with(1,'failed')
                self.assertFalse(app.busy)

    async def test_command_usage_errors_remain_readable(self):
        store=Mock()
        store.next_update.side_effect=[(1, {'text':'/language bad', 'chat':{'id':42}}), asyncio.CancelledError()]
        app=SimpleNamespace(store=store, inbox_event=asyncio.Event(), busy=False,
                            handle=AsyncMock(side_effect=ValueError('用法：/language CH EN JP')), notify=AsyncMock())
        with self.assertRaises(asyncio.CancelledError):
            await App.inbox_worker(app)
        self.assertEqual(app.notify.await_args.args[1],'用法：/language CH EN JP')
