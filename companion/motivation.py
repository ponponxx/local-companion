"""EMA-inspired motivation and coping layer for response selection.

The psychological theories provide the process shape, not calibrated scores.
This module keeps engineering heuristics inspectable and deterministic.  It
turns many state inputs into one dominant goal and one coping intention before
the response Picker chooses communicative acts.
"""
from __future__ import annotations

from .adult_intimacy import adult_goal_actionable, erotic_readiness
from .emotions import normalize_emotions


NEED_DESCRIPTIONS = {
    "relatedness": "維持連結並理解重要的人",
    "autonomy": "保有選擇、界線與尊嚴",
    "competence": "有效處理眼前問題",
    "curiosity": "理解未知資訊與對方",
    "homeostasis": "恢復精力並降低過度負荷",
    "erotic_connection": "在雙方當下同意的成人情境中尋求情色連結",
}

GOAL_DESCRIPTIONS = {
    "maintain_connection": "maintain the relationship by addressing what matters in this turn",
    "preserve_agency": "preserve the character's agency, boundaries, and dignity",
    "respond_effectively": "handle the user's current message effectively",
    "understand_user": "understand the user and the current situation",
    "restore_capacity": "limit overload and conserve the character's capacity",
    "advance_open_loop": "advance the relevant unfinished conversational matter",
    "seek_erotic_connection": "continue the mutually desired adult intimate exchange within the confirmed boundary",
}

COPING_DESCRIPTIONS = {
    "problem_solve": "address the user's concrete problem first",
    "seek_information": "obtain only the information necessary to proceed",
    "offer_support": "respond to the present feeling with proportionate support",
    "repair_connection": "repair the specific relationship obstacle or misunderstanding",
    "conserve_capacity": "keep the reply brief and avoid adding new demands",
    "explore": "acknowledge the content and explore one meaningful unknown",
    "engage": "respond directly to the current message",
}


def _clamp(value):
    return round(max(0.0, min(100.0, float(value))), 1)


def evaluate_motivation(strategy, traits, relationship, state, conversation=None, user_affect=None,
                        need_weights=None, adult_intimacy=None, adult_policy=None, intimacy_state=None):
    """Return needs -> goals -> appraisal -> coping for a single event.

    Need activation means current motivational pressure, not a personality
    score or a psychological measurement.  Goals are recomputed from durable
    sources each turn, so conversation open loops and current state remain the
    source of truth instead of duplicating them in another database.
    """
    conversation = conversation or {}
    own = normalize_emotions(state.get("emotions"))
    user = normalize_emotions((user_affect or {}).get("emotions"))
    user_distress = max(user[key] for key in ("sadness", "anger", "fear", "disgust"))
    adult = adult_intimacy or {}

    raw_needs = {
        "relatedness": _clamp(20 + relationship["affection"] * .25 + relationship["tension"] * .45
                              + user_distress * .15),
        "autonomy": _clamp(15 + traits["assertiveness"] * .35 + (100 - relationship["respect"]) * .25
                           + own["anger"] * .25 + own["disgust"] * .15),
        "competence": _clamp(20 + traits["conscientiousness"] * .40 + state["stress"] * .10),
        "curiosity": _clamp(traits["openness"] * .55 + state["energy"] * .15),
        "homeostasis": _clamp((100 - state["energy"]) * .65 + state["stress"] * .35),
        "erotic_connection": erotic_readiness(adult, relationship, state),
    }
    need_weights = need_weights or {}
    needs = {}
    for key, value in raw_needs.items():
        weight = need_weights.get(key, 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            weight = 1.0
        needs[key] = _clamp(value * max(.25, min(2.0, float(weight))))

    route_boosts = {
        "relatedness": 35 if strategy in {"support", "repair", "followup", "playful"} else 0,
        "autonomy": 25 if strategy == "repair" else 10 if strategy == "share_self" else 0,
        "competence": 45 if strategy in {"direct_answer", "clarify"} else 15,
        "curiosity": 30 if strategy in {"followup", "clarify"} else 10,
        "homeostasis": 20 if state["energy"] < 30 else 0,
        "erotic_connection": 20 if adult_goal_actionable(
            adult_policy or {}, intimacy_state or {}, relationship, strategy) else 0,
    }
    specifications = (
        ("maintain_connection", "relatedness", "user", relationship["trust"]),
        ("preserve_agency", "autonomy", "self", traits["assertiveness"]),
        ("respond_effectively", "competence", "current_message", traits["conscientiousness"]),
        ("understand_user", "curiosity", "current_situation", traits["openness"]),
        ("restore_capacity", "homeostasis", "self", 100 - state["stress"]),
    )
    goals = [{
        "id": goal_id,
        "source_need": need,
        "target": target,
        "intensity": _clamp(needs[need] + route_boosts[need]),
        "commitment": _clamp(commitment),
        "status": "active",
    } for goal_id, need, target, commitment in specifications]

    erotic_active = adult_goal_actionable(adult_policy or {}, intimacy_state or {}, relationship, strategy)
    goals.append({
        "id": "seek_erotic_connection",
        "source_need": "erotic_connection",
        "target": "mutual_adult_intimacy",
        "intensity": _clamp(needs["erotic_connection"] + route_boosts["erotic_connection"]),
        "commitment": _clamp(adult.get("erotic_expressiveness", 50)),
        "status": "active" if erotic_active else "latent",
    })

    loops = conversation.get("open_loops")
    if isinstance(loops, list) and loops and str(loops[0]).strip():
        goals.append({
            "id": "advance_open_loop",
            "source_need": "competence",
            "target": " ".join(str(loops[0]).split())[:120],
            "intensity": _clamp(needs["competence"] + 20),
            "commitment": _clamp(traits["conscientiousness"]),
            "status": "active",
        })
    goals.sort(key=lambda item: (item["status"] == "active", item["intensity"], item["commitment"]), reverse=True)
    dominant = next(item for item in goals if item["status"] == "active")

    tension = relationship["tension"]
    if strategy == "repair" or tension >= 60:
        congruence = "obstructed"
    elif user_distress >= 50:
        congruence = "threatened"
    elif strategy in {"direct_answer", "clarify"}:
        congruence = "unresolved"
    else:
        congruence = "opportunity"
    control = _clamp(state["energy"] * .35 + (100 - state["stress"]) * .25
                     + traits["conscientiousness"] * .40)
    urgency = _clamp(max(tension, user_distress) * .70
                     + (35 if strategy in {"repair", "direct_answer", "clarify"} else 10))
    agency = "shared" if tension >= 60 else "situation" if strategy in {"direct_answer", "clarify"} else "unknown"
    appraisal = {
        "goal_id": dominant["id"],
        "relevance": dominant["intensity"],
        "congruence": congruence,
        "control": control,
        "agency": agency,
        "urgency": urgency,
    }

    if strategy == "direct_answer":
        coping = "problem_solve"
    elif strategy == "clarify":
        coping = "seek_information"
    elif user_distress >= 50 and strategy == "support":
        coping = "offer_support"
    elif strategy == "repair" or tension >= 60:
        coping = "repair_connection"
    elif state["energy"] < 25 or needs["homeostasis"] >= 75:
        coping = "conserve_capacity"
    elif strategy == "followup" and needs["curiosity"] >= 45:
        coping = "explore"
    else:
        coping = "engage"
    return {
        "model": "ema_lite_v1",
        "needs": needs,
        "erotic_readiness": {
            "score": needs["erotic_connection"],
            "actionable": erotic_active,
            "gate": "within_standing_policy" if erotic_active else "outside_policy_or_not_ready",
        },
        "goals": goals,
        "dominant_goal": dominant,
        "appraisal": appraisal,
        "coping": {"strategy": coping, "mode": (
            "problem_focused" if coping in {"problem_solve", "seek_information", "repair_connection"}
            else "capacity_regulation" if coping == "conserve_capacity" else "relationship_focused"
        )},
    }


def motivation_contract(motivation):
    """Compile internal motivation into qualitative Writer instructions."""
    goal = motivation.get("dominant_goal", {}).get("id", "respond_effectively")
    coping = motivation.get("coping", {}).get("strategy", "engage")
    return GOAL_DESCRIPTIONS.get(goal, GOAL_DESCRIPTIONS["respond_effectively"]), \
        COPING_DESCRIPTIONS.get(coping, COPING_DESCRIPTIONS["engage"])
