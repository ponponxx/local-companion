"""Interactive credential setup: token stays in the local terminal and .env."""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import shutil
import sys

import httpx
from dotenv import set_key

ROOT = Path(__file__).resolve().parent.parent


def main():
    os.umask(0o077)
    token = getpass.getpass("貼上 @BotFather 提供的 Bot Token（輸入不顯示）：").strip()
    if not token:
        sys.exit("未輸入 token，設定未變更")
    with httpx.Client(trust_env=False, timeout=40) as client:
        base = f"https://api.telegram.org/bot{token}"
        try:
            me = client.post(base + "/getMe").json()
            if not me.get("ok"):
                sys.exit("Token 無效，設定未變更")
            webhook = client.post(base + "/getWebhookInfo").json()
            if webhook.get("result", {}).get("url"):
                sys.exit("此 Bot 已設定 webhook，請先確認用途並移除；設定未變更")
            print(f"請在 Telegram 私訊 @{me['result']['username']} 並傳 /start。")
            print("等待訊息；若機器人主程式正在執行，請先停止它。")
            updates = client.post(base + "/getUpdates", json={"timeout": 30, "allowed_updates": ["message"]}).json()
        except (httpx.HTTPError, ValueError, KeyError):
            sys.exit("Telegram 連線或資料格式錯誤，設定未變更")
        candidates = {item["message"]["from"]["id"] for item in updates.get("result", [])
                      if item.get("message", {}).get("chat", {}).get("type") == "private" and "from" in item["message"]}
        if candidates:
            print("最近私訊帳號 ID：" + "、".join(str(user) for user in sorted(candidates)))
        value = input("輸入你自己的數字帳號 ID（僅允許此帳號使用）：").strip()
        if not value.isdecimal() or int(value) <= 0:
            sys.exit("帳號 ID 無效，設定未變更")
        target = ROOT / ".env"
        if not target.exists():
            shutil.copyfile(ROOT / ".env.example", target)
        target.chmod(0o600)
        set_key(target, "TELEGRAM_BOT_TOKEN", token)
        set_key(target, "TELEGRAM_ALLOWED_USER_ID", value)
        print("設定已存入本機 .env。可啟動聊天模型與 Telegram 服務。")


if __name__ == "__main__":
    main()
