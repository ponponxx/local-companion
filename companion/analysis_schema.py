"""Machine-readable contract for the single background turn analyzer."""
from .adult_intimacy import ADULT_EVENT_ENUMS
from .emotions import EMOTION_LABELS


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required, 'additionalProperties': False}


TEXT = {'type': 'string'}
NUMBER = {'type': 'number'}
ANALYSIS_SCHEMA = obj({
    'changes': obj({key: NUMBER for key in (
        'familiarity', 'trust', 'affection', 'comfort', 'respect', 'intimacy', 'tension', 'initiative'
    )}, required=[]),
    'reason': TEXT,
    'profile_ops': {'type': 'array', 'maxItems': 2, 'items': obj({
        'action': {'enum': ['add', 'update', 'delete']}, 'id': {'type': 'integer'},
        'category': TEXT, 'value': TEXT,
    }, required=['action'])},
    'conversation_state': obj({
        'topic': TEXT, 'scene_status': {'enum': ['none', 'active', 'closed']}, 'scene': TEXT,
        'last_user_intent': TEXT, 'last_assistant_action': TEXT,
        'open_loops': {'type': 'array', 'maxItems': 3, 'items': TEXT},
    }),
    'adult_intimacy_event': obj({
        **{key: {'enum': list(values)} for key, values in ADULT_EVENT_ENUMS.items()},
        'user_evidence': TEXT,
        'assistant_evidence': TEXT,
    }),
    'engine_update': obj({
        'evidence': TEXT,
        'state': obj({
            'energy_delta': NUMBER, 'stress_delta': NUMBER,
            'emotion_deltas': obj({key: {'type': 'number', 'minimum': -10, 'maximum': 10}
                                  for key in EMOTION_LABELS}, required=[]),
            'location': TEXT, 'activity': TEXT, 'availability': {'enum': ['free', 'busy', 'sleep']},
        }, required=[]),
        'facts': {'type': 'array', 'maxItems': 3, 'items': obj({'key': TEXT, 'value': TEXT, 'evidence': TEXT})},
        'knowledge_ops': {'type': 'array', 'maxItems': 4, 'items': obj({
            'action': {'enum': ['disclosed', 'acknowledged']}, 'fact_id': {'type': 'integer'}, 'evidence': TEXT,
        })},
    }),
})

CHARACTER_SCHEMA = ANALYSIS_SCHEMA['properties'].pop('engine_update')
ANALYSIS_SCHEMA['required'].remove('engine_update')
