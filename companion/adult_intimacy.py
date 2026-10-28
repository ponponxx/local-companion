"""Adult-intimacy policy, dynamic pacing, and bounded relationship feedback.

The persistent policy is the user's standing interaction envelope. Dynamic
heat is an observation for pacing, never permission or a model-editable setting.
This module does not generate dialogue.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

ADULT_TRAIT_DEFAULTS = {
    "flirtatiousness": 45, "sexual_openness": 55, "excitation_sensitivity": 50,
    "inhibition_sensitivity": 50, "responsive_desire": 65, "erotic_expressiveness": 45,
}
ADULT_TRAIT_LABELS = {
    "flirtatiousness": "挑逗傾向", "sexual_openness": "成人親密開放度",
    "excitation_sensitivity": "性興奮敏感度", "inhibition_sensitivity": "性抑制敏感度",
    "responsive_desire": "回應式欲望傾向", "erotic_expressiveness": "情色表達主動度",
}
ADULT_RELATIONSHIP_DEFAULTS = {"erotic_interest": 10, "sexual_comfort": 5, "intimate_safety": 50}
ADULT_RELATIONSHIP_LABELS = {
    "erotic_interest": "情色興趣", "sexual_comfort": "性話題自在", "intimate_safety": "親密安全感",
}
ADULT_NEED_DEFAULTS = {"erotic_connection": 1.0}
ADULT_NEED_LABELS = {"erotic_connection": "情色連結需求權重"}

ADULT_POLICY_MODES = ("off", "flirt", "intimate")
ADULT_POLICY_LABELS = {"off": "關閉", "flirt": "自然曖昧", "intimate": "親密升溫"}
MODE_MAX_HEAT = {"off": 0.0, "flirt": 40.0, "intimate": 100.0}
ADULT_POLICY_DEFAULTS = {"mode": "off", "agent_may_initiate": True}
INTIMACY_STATE_DEFAULTS = {
    "heat": 0.0, "desire": 0.0, "receptivity": 20.0, "tension": 0.0,
    "inhibition": 50.0, "momentum": 0.0, "mutuality": 0.0,
    "last_signal": "neutral", "last_direction": "none", "reason": "尚無親密互動訊號",
}
INTIMACY_SIGNALS = ("neutral", "flirt", "intimate", "receptive", "disengage", "stop")
INTIMACY_DIRECTIONS = ("none", "warm_tease", "playful_probe", "deepen_intimacy", "hold", "cool_down")

ADULT_EVENT_ENUMS = {
    "kind": ("none", "flirtation", "sexting"),
    "participation": ("none", "mutual", "user_only", "assistant_only", "unclear"),
    "consent": ("none", "explicit", "affirmed", "withdrawn", "unclear"),
    "completion": ("none", "ongoing", "completed", "interrupted", "unclear"),
    "boundary": ("none", "respected", "violated", "unclear"),
}


def bounded_score(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = fallback
    return round(max(0.0, min(100.0, float(value))), 2)


def normalized_policy(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = value or {}
    mode = value.get("mode") if value.get("mode") in ADULT_POLICY_MODES else "off"
    return {"mode": mode, "agent_may_initiate": bool(value.get("agent_may_initiate", True)),
            "maximum_heat": MODE_MAX_HEAT[mode]}


def normalized_intimacy_state(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = value or {}
    state = {key: bounded_score(value.get(key), default)
             for key, default in INTIMACY_STATE_DEFAULTS.items() if isinstance(default, float)}
    state["last_signal"] = value.get("last_signal") if value.get("last_signal") in INTIMACY_SIGNALS else "neutral"
    state["last_direction"] = value.get("last_direction") if value.get("last_direction") in INTIMACY_DIRECTIONS else "none"
    reason = value.get("reason")
    state["reason"] = " ".join(reason.split())[:160] if isinstance(reason, str) and reason.strip() else INTIMACY_STATE_DEFAULTS["reason"]
    return state


@dataclass(frozen=True, slots=True)
class AdultIntimacyTraits:
    flirtatiousness: float
    sexual_openness: float
    excitation_sensitivity: float
    inhibition_sensitivity: float
    responsive_desire: float
    erotic_expressiveness: float

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None = None) -> "AdultIntimacyTraits":
        values = values or {}
        return cls(**{key: bounded_score(values.get(key), default)
                      for key, default in ADULT_TRAIT_DEFAULTS.items()})


_STOP = re.compile(r"(?:不要|別|停止|停下|不舒服|不想(?:聊|玩|繼續)|到此為止|stop|don't|do not|no more|やめて|그만)", re.I)
_DISENGAGE = re.compile(r"(?:先不聊這個|跳過這個|聊別的|算了|改天再說|not now|change the subject|later|話題を変|나중에)", re.I)
_INTIMATE = re.compile(r"(?:色色|色情|成人互動|親密互動|性慾|做愛|上床|裸|脫(?:掉|衣)|摸我|想要你|sext|sex|horny|nude|naked|エッチ|セックス|裸|섹스|야한)", re.I)
_FLIRT = re.compile(r"(?:曖昧|撩我|挑逗|調情|害羞|心動|抱抱|親我|吻我|想你|喜歡你|誘惑|flirt|tease me|kiss me|抱きしめ|キス|好き|키스|안아|좋아해)", re.I)
_RECEPTIVE = re.compile(r"(?:可以繼續|繼續吧|再多一點|我喜歡這樣|就這樣|不要停|來吧|keep going|don't stop|more like that|I like that|go on|続けて|もっと|계속|더 해)", re.I)


def observe_intimacy_signal(text: str, current: Mapping[str, Any] | None = None) -> str:
    """High-precision deterministic signal; neutral is deliberately common."""
    text = " ".join(str(text).split())[:3500]
    if (current or {}).get("heat", 0) >= 8 and _RECEPTIVE.search(text): return "receptive"
    if _STOP.search(text): return "stop"
    if _DISENGAGE.search(text): return "disengage"
    if _INTIMATE.search(text): return "intimate"
    if _FLIRT.search(text): return "flirt"
    return "neutral"


def erotic_readiness(adult: Mapping[str, Any], relationship: Mapping[str, Any], state: Mapping[str, Any]) -> float:
    """Motivation pressure; this score never grants permission."""
    return bounded_score(
        float(relationship.get("erotic_interest", 0)) * .25 + float(relationship.get("sexual_comfort", 0)) * .15
        + float(relationship.get("intimate_safety", 0)) * .10 + float(adult.get("responsive_desire", 50)) * .15
        + float(adult.get("sexual_openness", 50)) * .10 + float(adult.get("excitation_sensitivity", 50)) * .15
        + float(adult.get("flirtatiousness", 50)) * .10 + float(state.get("energy", 50)) * .10
        - float(adult.get("inhibition_sensitivity", 50)) * .15 - float(state.get("stress", 0)) * .10)


def advance_intimacy_state(current: Mapping[str, Any] | None, policy: Mapping[str, Any] | None,
                           adult: Mapping[str, Any], relationship: Mapping[str, Any],
                           physical: Mapping[str, Any], user_text: str) -> dict[str, Any]:
    """Apply the user's latest signal before response planning."""
    state, envelope = normalized_intimacy_state(current), normalized_policy(policy)
    signal = observe_intimacy_signal(user_text, state)
    if envelope["mode"] == "off":
        return {**normalized_intimacy_state(), "last_signal": signal, "reason": "成人互動模式已關閉"}
    readiness = erotic_readiness(adult, relationship, physical)
    desire = bounded_score(readiness * .65 + state["momentum"] * .20 + state["receptivity"] * .15)
    inhibition = bounded_score(float(adult.get("inhibition_sensitivity", 50)) * .45
        + float(physical.get("stress", 0)) * .30 + float(relationship.get("tension", 0)) * .35
        + max(0.0, 35 - float(relationship.get("intimate_safety", 0))) * .40
        - float(physical.get("energy", 50)) * .10)
    heat, receptivity, tension = state["heat"], state["receptivity"], state["tension"]
    momentum, mutuality = state["momentum"], state["mutuality"]
    reason = "本輪沒有明確親密訊號，熱度自然回落"
    if signal == "stop":
        heat, receptivity, tension, momentum, mutuality = 0, 0, 0, 0, 0
        reason = "使用者明確停止，立即結束親密升溫"
    elif signal == "disengage":
        heat, receptivity = max(0, heat - 18), max(0, receptivity - 15)
        tension, momentum, mutuality = max(0, tension - 10), 0, max(0, mutuality - 15)
        reason = "使用者轉移或暫停親密話題"
    elif signal == "intimate":
        heat += 18 if envelope["mode"] == "intimate" else 7
        receptivity, tension, momentum = receptivity + 16, tension + 10, momentum + 15
        mutuality += 8 if state["last_direction"] != "none" else 2
        reason = "使用者主動給出成人親密訊號"
    elif signal == "flirt":
        heat, receptivity, tension, momentum = heat + 10, receptivity + 10, tension + 7, momentum + 10
        mutuality += 6 if state["last_direction"] != "none" else 2
        reason = "使用者給出曖昧或挑逗訊號"
    elif signal == "receptive":
        heat, receptivity, tension, momentum, mutuality = heat + 7, receptivity + 12, tension + 5, momentum + 12, mutuality + 12
        reason = "使用者承接最近的親密語氣"
    else:
        heat, tension = max(0, heat - 3), max(0, tension - 2)
        receptivity, momentum, mutuality = max(0, receptivity - 2), max(0, momentum - 4), max(0, mutuality - 3)
    return {"heat": round(min(envelope["maximum_heat"], bounded_score(heat)), 2), "desire": desire,
            "receptivity": bounded_score(receptivity), "tension": bounded_score(tension),
            "inhibition": inhibition, "momentum": bounded_score(momentum), "mutuality": bounded_score(mutuality),
            "last_signal": signal, "last_direction": state["last_direction"], "reason": reason}


def choose_intimacy_direction(policy: Mapping[str, Any] | None, intimate_state: Mapping[str, Any] | None,
                               relationship: Mapping[str, Any], user_distress: float = 0.0) -> dict[str, Any]:
    envelope, state = normalized_policy(policy), normalized_intimacy_state(intimate_state)
    signal = state["last_signal"]
    if envelope["mode"] == "off": direction, reason = "none", "成人互動模式關閉"
    elif signal in {"stop", "disengage"} or relationship.get("tension", 0) >= 50 or user_distress >= 50:
        direction, reason = "cool_down", "停止、轉題或不適合升溫的訊號優先"
    elif signal == "intimate" and envelope["mode"] == "intimate":
        direction, reason = "deepen_intimacy", "沿著使用者主動的親密訊號前進一小步"
    elif signal in {"flirt", "receptive", "intimate"}:
        direction, reason = "playful_probe", "承接使用者的曖昧訊號但不跳級"
    elif state["heat"] >= 18 and state["momentum"] >= 12:
        direction, reason = "hold", "維持已有張力，這一輪不再加碼"
    elif (envelope["agent_may_initiate"] and state["last_direction"] not in {"warm_tease", "playful_probe", "deepen_intimacy"}
          and state["desire"] >= 48 and state["inhibition"] < 55
          and relationship.get("intimate_safety", 0) >= 35 and relationship.get("tension", 0) < 30):
        direction, reason = "warm_tease", "角色動機與關係允許自然加入輕微曖昧"
    else: direction, reason = "none", "本輪維持一般對話"
    return {"direction": direction, "reason": reason, "mode": envelope["mode"],
            "heat_band": "low" if state["heat"] < 25 else "medium" if state["heat"] < 65 else "high"}


def finalize_intimacy_state(current: Mapping[str, Any] | None, policy: Mapping[str, Any] | None,
                            direction: str) -> dict[str, Any]:
    """Record the delivered response direction without changing permissions."""
    state, envelope = normalized_intimacy_state(current), normalized_policy(policy)
    if envelope["mode"] == "off": return {**normalized_intimacy_state(), "reason": "成人互動模式已關閉"}
    if direction == "warm_tease": state["heat"], state["tension"], state["momentum"] = state["heat"] + 4, state["tension"] + 5, state["momentum"] + 4
    elif direction == "playful_probe":
        state["heat"], state["tension"], state["momentum"] = state["heat"] + 5, state["tension"] + 4, state["momentum"] + 6
        if state["last_signal"] in {"flirt", "intimate", "receptive"}: state["mutuality"] += 7
    elif direction == "deepen_intimacy":
        state["heat"], state["momentum"] = state["heat"] + 7, state["momentum"] + 8
        if state["last_signal"] in {"intimate", "receptive"}: state["mutuality"] += 10
    elif direction == "cool_down":
        state["heat"], state["momentum"], state["tension"] = max(0, state["heat"] - 20), 0, max(0, state["tension"] - 12)
    elif direction == "none": state["momentum"] = max(0, state["momentum"] - 2)
    for key in ("heat", "desire", "receptivity", "tension", "inhibition", "momentum", "mutuality"):
        state[key] = bounded_score(state[key])
    state["heat"] = min(envelope["maximum_heat"], state["heat"])
    state["last_direction"] = direction if direction in INTIMACY_DIRECTIONS else "none"
    return state


def adult_goal_actionable(policy: Mapping[str, Any], intimate_state: Mapping[str, Any],
                          relationship: Mapping[str, Any], strategy: str = "") -> bool:
    envelope, state = normalized_policy(policy), normalized_intimacy_state(intimate_state)
    return bool(envelope["mode"] != "off" and state["last_signal"] not in {"stop", "disengage"}
                and float(relationship.get("intimate_safety", 0)) >= 35
                and (state["heat"] >= 8 or state["desire"] >= 48))


def adult_event_changes(user_text: str, assistant_text: str, event: Mapping[str, Any] | None) -> tuple[dict[str, float], str]:
    """Evidence-based relationship feedback, separate from conversational pacing."""
    if not isinstance(event, Mapping): return {}, ""
    user_evidence = " ".join(str(event.get("user_evidence", "")).split())[:240]
    assistant_evidence = " ".join(str(event.get("assistant_evidence", "")).split())[:240]
    valid = bool(user_evidence and assistant_evidence and user_evidence in user_text and assistant_evidence in assistant_text)
    if event.get("consent") == "withdrawn" or event.get("boundary") == "violated":
        if not (user_evidence and user_evidence in user_text): return {}, ""
        return {"sexual_comfort": -3.0, "intimate_safety": -5.0, "comfort": -1.0, "tension": 4.0}, "親密互動中出現停止訊號或界線未被尊重"
    if (not valid or event.get("participation") != "mutual" or event.get("consent") not in {"explicit", "affirmed"}
            or event.get("boundary") != "respected"): return {}, ""
    if event.get("kind") == "sexting" and event.get("completion") == "completed":
        return {"familiarity": .4, "comfort": .3, "intimacy": .8, "erotic_interest": .3,
                "sexual_comfort": 2.0, "intimate_safety": .6}, "雙方承接且完成一段成人訊息親密互動"
    if event.get("kind") == "flirtation" and event.get("completion") in {"completed", "ongoing"}:
        return {"familiarity": .1, "erotic_interest": .25, "sexual_comfort": .4,
                "intimate_safety": .15}, "雙方自然承接的挑逗互動"
    return {}, ""
