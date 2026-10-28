from __future__ import annotations

import json
import re
from datetime import datetime


def parse_clock(value: str) -> int:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("時間格式須為 HH:MM")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    current, begin, finish = now.hour * 60 + now.minute, parse_clock(start), parse_clock(end)
    if begin == finish:
        return False
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish


def _json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("模型沒有回傳有效 JSON")


def parse_daily_plan(text: str) -> list[dict]:
    data = _json_object(text)
    activities = data.get("activities")
    if not isinstance(activities, list) or not 5 <= len(activities) <= 12:
        raise ValueError("每日行程須有合理數量的項目")
    result, seen, supplied_share = [], set(), False
    for raw in activities:
        if not isinstance(raw, dict):
            raise ValueError("每日行程項目格式無效")
        start = raw.get("start")
        minute = parse_clock(start) if isinstance(start, str) else -1
        if minute in seen:
            raise ValueError("每日行程時間不可重複")
        seen.add(minute)
        item = {"start": start, "start_minute": minute}
        for key in ("activity", "location", "outcome", "mood"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"每日行程缺少 {key}")
            item[key] = " ".join(value.split())[:160]
        for key in ("clothing", "accessories"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"每日行程缺少 {key}")
            item[key] = " ".join(value.split())[:200]
        availability = raw.get("availability")
        if availability not in {"free", "busy", "sleep"}:
            raise ValueError("availability 須為 free、busy 或 sleep")
        if "share_worthy" in raw:
            supplied_share = True
        if "share_worthy" in raw and not isinstance(raw["share_worthy"], bool):
            raise ValueError("share_worthy 須為布林值")
        item["availability"] = availability
        item["share_worthy"] = raw.get("share_worthy", False)
        result.append(item)
    result.sort(key=lambda item: item["start_minute"])
    if result[0]["start_minute"] != 0:
        result.insert(0, {
            "start": "00:00", "start_minute": 0, "activity": "睡覺", "location": "家中",
            "outcome": "休息並恢復精神", "mood": "平靜", "availability": "sleep",
            "share_worthy": False, "clothing": "舒適睡衣", "accessories": "無",
        })
    if not supplied_share:
        candidates = [item for item in result if item["availability"] == "free" and item["start_minute"] >= 9 * 60]
        for item in candidates[:2]:
            item["share_worthy"] = True
    return result


def parse_proactive_decision(text: str) -> dict:
    data = _json_object(text)
    if not isinstance(data.get("send"), bool):
        raise ValueError("主動訊息判斷缺少 send")
    message, reason = data.get("message", ""), data.get("reason", "")
    if not isinstance(message, str) or not isinstance(reason, str):
        raise ValueError("主動訊息判斷格式無效")
    message = " ".join(message.split())[:500]
    reason = " ".join(reason.split())[:160]
    if data["send"] and not message:
        raise ValueError("主動訊息內容不可為空")
    return {"send": data["send"], "message": message, "reason": reason}
