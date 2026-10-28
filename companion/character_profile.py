"""Reusable character aggregate consumed by the decision pipeline.

Storage, Telegram, and model providers are deliberately absent here.  A new
character can be constructed from a plain mapping and passed to the same
thinking modules without giving those modules access to the database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .adult_intimacy import ADULT_NEED_DEFAULTS, ADULT_TRAIT_DEFAULTS, AdultIntimacyTraits
from .emotions import normalize_emotions, emotion_mood


DEFAULT_NEED_WEIGHTS = {
    "relatedness": 1.0,
    "autonomy": 1.0,
    "competence": 1.0,
    "curiosity": 1.0,
    "homeostasis": 1.0,
    **ADULT_NEED_DEFAULTS,
}

# Compatibility alias for existing imports and saved character templates.
DEFAULT_ADULT_INTIMACY = ADULT_TRAIT_DEFAULTS

DEFAULT_CHARACTER_TRAITS = {
    "openness": 65,
    "conscientiousness": 55,
    "extraversion": 55,
    "agreeableness": 72,
    "emotional_stability": 68,
    "impulsiveness": 40,
    "assertiveness": 55,
    "teasing": 40,
    "expressiveness": 60,
}


def _text(value: Any, fallback: str = "", limit: int = 800) -> str:
    value = " ".join(str(value if value is not None else fallback).split())
    return value[:limit]


def _score(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = fallback
    return round(max(0.0, min(100.0, float(value))), 2)


@dataclass(frozen=True, slots=True)
class CharacterStory:
    name: str
    background: str
    personality: str
    tone: str


@dataclass(frozen=True, slots=True)
class CharacterAffect:
    emotions: dict[str, float]
    mood: str


@dataclass(frozen=True, slots=True)
class CharacterPhysical:
    energy: float
    stress: float
    location: str
    activity: str
    availability: str


@dataclass(frozen=True, slots=True)
class CharacterAppearance:
    body: str
    clothing: str
    accessories: str


@dataclass(frozen=True, slots=True)
class CharacterMotivationParameters:
    need_weights: dict[str, float]


@dataclass(frozen=True, slots=True)
class CharacterProfile:
    """One character's stable definition plus current embodied state."""
    story: CharacterStory
    traits: dict[str, float]
    affect: CharacterAffect
    motivation: CharacterMotivationParameters
    adult_intimacy: AdultIntimacyTraits
    appearance: CharacterAppearance
    physical: CharacterPhysical

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CharacterProfile":
        story = payload.get("story") if isinstance(payload.get("story"), Mapping) else {}
        affect = payload.get("affect") if isinstance(payload.get("affect"), Mapping) else {}
        physical = payload.get("physical") if isinstance(payload.get("physical"), Mapping) else {}
        appearance = payload.get("appearance") if isinstance(payload.get("appearance"), Mapping) else {}
        motivation = payload.get("motivation") if isinstance(payload.get("motivation"), Mapping) else {}
        raw_traits = payload.get("traits") if isinstance(payload.get("traits"), Mapping) else {}
        raw_adult = payload.get("adult_intimacy") if isinstance(payload.get("adult_intimacy"), Mapping) else {}
        raw_emotions = normalize_emotions(affect.get("emotions"))
        raw_weights = motivation.get("need_weights") if isinstance(motivation.get("need_weights"), Mapping) else {}
        weights = {}
        for key, default in DEFAULT_NEED_WEIGHTS.items():
            value = raw_weights.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = default
            weights[key] = round(max(0.25, min(2.0, float(value))), 2)
        availability = physical.get("availability")
        if availability not in {"free", "busy", "sleep"}:
            availability = "free"
        return cls(
            story=CharacterStory(
                name=_text(story.get("name"), "小伴"),
                background=_text(story.get("background"), "陪伴使用者的聊天夥伴"),
                personality=_text(story.get("personality"), "親切、自然、有自己的觀點"),
                tone=_text(story.get("tone"), "使用繁體中文，簡潔口語"),
            ),
            traits={key: _score(raw_traits.get(key), default)
                    for key, default in DEFAULT_CHARACTER_TRAITS.items()},
            affect=CharacterAffect(
                emotions=raw_emotions,
                mood=_text(affect.get("mood"), emotion_mood(raw_emotions), 100),
            ),
            motivation=CharacterMotivationParameters(need_weights=weights),
            adult_intimacy=AdultIntimacyTraits.from_mapping(raw_adult),
            appearance=CharacterAppearance(
                body=_text(appearance.get("body")),
                clothing=_text(appearance.get("clothing"), "依場合選擇自然、日常的服裝"),
                accessories=_text(appearance.get("accessories"), "少量、符合場合的裝飾"),
            ),
            physical=CharacterPhysical(
                energy=_score(physical.get("energy"), 70),
                stress=_score(physical.get("stress"), 20),
                location=_text(physical.get("location"), "未確認", 100),
                activity=_text(physical.get("activity"), "未確認", 100),
                availability=availability,
            ),
        )

    @classmethod
    def from_store(cls, store, now=None) -> "CharacterProfile":
        state = store.engine.state(now)
        return cls.from_dict({
            "story": store.persona(),
            "traits": store.traits(),
            "affect": {"emotions": state.get("emotions"), "mood": state.get("mood")},
            "motivation": {"need_weights": store.need_weights()},
            "adult_intimacy": store.adult_intimacy(),
            "appearance": store.appearance(),
            "physical": {key: state.get(key) for key in (
                "energy", "stress", "location", "activity", "availability")},
        })

    def state_dict(self) -> dict[str, Any]:
        return {
            "energy": self.physical.energy,
            "stress": self.physical.stress,
            "location": self.physical.location,
            "activity": self.physical.activity,
            "availability": self.physical.availability,
            "mood": self.affect.mood,
            "emotions": dict(self.affect.emotions),
        }

    def story_dict(self) -> dict[str, str]:
        return asdict(self.story)

    def adult_intimacy_dict(self) -> dict[str, float]:
        return asdict(self.adult_intimacy)

    def decision_snapshot(self) -> dict[str, Any]:
        """Detached copy safe to pass to a rule engine or bounded model."""
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
