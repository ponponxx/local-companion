from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
import sys

import httpx

from .app import App
from .config import Config
from .services import Images, LLM, ServiceError, Telegram, Voice
from .store import Store


async def main(args):
    config = Config.load(require_credentials=not args.check)
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with httpx.AsyncClient(trust_env=False) as telegram_client, httpx.AsyncClient(trust_env=False) as local_client:
        llm, voice, images = LLM(config, local_client), Voice(config), Images(config)
        if args.check:
            print(json.dumps({"credentials_configured": bool(config.token and config.allowed_user > 0),
                              "llm_reachable": await llm.healthy(), "chat_core": config.chat_core, "voice": voice.readiness(),
                              "image_python_exists": config.image_python.is_file(), "image_runtime_installed": await images.installed(), "image_model": config.image_model,
                              "data_dir": str(config.data_dir)}, ensure_ascii=False, indent=2))
            return
        with (config.data_dir / "bot.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ServiceError("已有另一個機器人程序執行中") from None
            store = Store(config.data_dir / "memory.sqlite3")
            app = App(config, store, Telegram(config.token, telegram_client), llm, voice, images)
            from .diagnostics import Diagnostics,DiagnosticLogHandler
            archive=None;archive_handler=None
            try:
                archive=Diagnostics(config.data_dir/'diagnostics.sqlite3')
                archive.recover_interrupted()
                archive.import_available(store.db)
                app.diagnostics=llm.diagnostics=images.diagnostics=app.telegram.diagnostics=archive
                archive_handler=DiagnosticLogHandler(archive)
                logging.getLogger().addHandler(archive_handler)
            except Exception:
                logging.getLogger('companion').warning('本機診斷資料庫未能啟用；聊天服務繼續運作')
            try:
                await app.run()
            finally:
                if app.router:
                    await app.router.client.close()
                if app.pyramid:
                    await app.pyramid.embedder.close()
                if archive_handler:logging.getLogger().removeHandler(archive_handler)
                if archive:archive.close()
                store.close()


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="check local readiness without contacting Telegram")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP logs can contain Telegram bot tokens in URL paths.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        asyncio.run(main(args))
    except (ValueError, ServiceError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"服務已停止（{type(exc).__name__}），請檢查本機設定與資料目錄", file=sys.stderr)
        sys.exit(1)
