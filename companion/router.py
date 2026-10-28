"""Semantic candidates -> deterministic eligibility/policy -> expression plan.

Similarity is never interpreted as a probability. The expression model may fill
content but may not change the selected communicative goal or invent state.
"""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import time

from .engine import behavior_policy
from .persona_policy import RulePersonaPolicy, apply_persona_policy, policy_options
from .emotions import observe_user_emotions
from .response_picker import pick_response
from .motivation import evaluate_motivation, motivation_contract
from .adult_intimacy import choose_intimacy_direction


CATALOG_PATH = Path(__file__).with_name('strategies.json')

VISUAL_INTENT_EXAMPLES = {
    'selfie': [
        '讓我看看你現在的模樣', '可以發一張你現在的照片嗎', '拍張自拍給我看',
        'show me what you look like right now',
    ],
    'pov': [
        '讓我看看你眼前的景色', '你那邊現在看起來是什麼樣子', '拍下你現在看到的畫面',
        'show me what you can see right now',
    ],
    'general': [
        '把剛才說的場景畫出來', '幫我生成那個畫面的圖片', '我想看一張這段故事的插圖',
        'turn that scene into an image',
    ],
    'none': [
        '我們來聊聊圖片生成技術', '你會畫圖嗎', '哪一個生圖模型比較好',
        'tell me about image generation models',
    ],
}


def load_catalog(path=CATALOG_PATH):
    catalog = json.loads(Path(path).read_text())
    ids = [item['id'] for item in catalog['strategies']] + [catalog['fallback']['id']]
    if len(ids) != len(set(ids)) or not all(item.get('examples') and item.get('outline') for item in catalog['strategies']):
        raise ValueError('invalid strategy catalog')
    return catalog


class EmbeddingClient:
    """Resident after first use, released after idle; never imports torch in bot."""
    def __init__(self, python, model_path, timeout=30, idle_seconds=600):
        self.python, self.model_path = Path(python), Path(model_path)
        self.timeout, self.idle_seconds = timeout, idle_seconds
        self.process = None
        self.lock = asyncio.Lock()
        self.last_used = 0.0

    async def encode(self, texts, prompt_name='Classification'):
        async with self.lock:
            try:
                if self.process is None or self.process.returncode is not None:
                    if not self.python.is_file() or not (self.model_path / 'config.json').is_file():
                        raise RuntimeError('embedding_not_installed')
                    self.process = await asyncio.create_subprocess_exec(
                        str(self.python), '-m', 'companion.embedding_worker', '--model-path', str(self.model_path),
                        cwd=Path(__file__).resolve().parent.parent,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, start_new_session=True, limit=2_000_000,
                        env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false'},
                    )
                self.process.stdin.write((json.dumps({'texts': texts, 'prompt_name': prompt_name}, ensure_ascii=False) + '\n').encode())
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), self.timeout)
                if not line:
                    raise RuntimeError('embedding_worker_exited')
                body = json.loads(line)
                vectors = body.get('vectors')
                if not isinstance(vectors, list) or len(vectors) != len(texts):
                    raise RuntimeError('invalid_embedding_output')
                dimension = len(vectors[0])
                if not dimension or any(len(v) != dimension or any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in v) for v in vectors):
                    raise RuntimeError('invalid_embedding_vector')
                self.last_used = time.monotonic()
                return vectors
            except BaseException:
                await self._close()
                raise

    async def _close(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def close(self):
        async with self.lock:
            await self._close()

    async def watch_idle(self):
        while True:
            await asyncio.sleep(30)
            async with self.lock:
                if self.process and time.monotonic() - self.last_used >= self.idle_seconds:
                    await self._close()


def select_plan(catalog, similarities, traits, relationship, state, knowledge, previous=(), user_affect=None,
                conversation=None, need_weights=None, adult_intimacy=None, adult_policy=None,
                intimacy_state=None):
    policy = behavior_policy(traits, relationship, state)
    items = {item['id']: item for item in catalog['strategies']}
    candidates = sorted(((key, score) for key, score in similarities.items() if key in items), key=lambda x: x[1], reverse=True)
    # Conservative retrieval gate. Abstentions are measured separately from wrong
    # decisions; the small fixture evaluation is not general accuracy calibration.
    selected = catalog['fallback']
    reasons = ['no_semantic_match']
    if candidates:
        first, score = candidates[0]
        margin = score - candidates[1][1] if len(candidates) > 1 else 1
        if score >= .40 and margin >= .025:
            selected, reasons = items[first], ['semantic_match']
    if selected['id'] == 'playful' and policy['priority'] == 'repair':
        selected, reasons = items['repair'], reasons + ['relationship_repair_coping']
    elif selected['id'] == 'playful' and (policy['priority'] != 'engage' or policy['teasing'] < 10):
        selected, reasons = items['support'], reasons + ['teasing_not_appropriate']
    if selected['id'] == 'playful' and user_affect and max(
        user_affect['emotions'][key] for key in ('sadness', 'anger', 'fear', 'disgust')
    ) >= 50:
        selected, reasons = items['support'], reasons + ['explicit_user_distress_blocks_teasing']
    if selected['id'] == 'share_self' and not any(k.get('value') and k['value'] != '未確認' for k in knowledge):
        selected, reasons = catalog['fallback'], reasons + ['no_verified_self_facts']
    motivation = evaluate_motivation(selected['id'], traits, relationship, state, conversation, user_affect,
                                     need_weights, adult_intimacy, adult_policy, intimacy_state)
    # EMA coping can change a socially optional route, but never suppress a
    # concrete answer, clarification, verified self-answer, or explicit support.
    if (motivation['coping']['strategy'] == 'repair_connection'
            and selected['id'] in {'playful', 'followup', 'open_response'}):
        selected, reasons = items['repair'], reasons + ['relationship_repair_coping']
        motivation = evaluate_motivation(selected['id'], traits, relationship, state, conversation, user_affect,
                                         need_weights, adult_intimacy, adult_policy, intimacy_state)
    # Repeated user needs retain their strategy; vary expression instead of
    # arbitrarily replacing comfort with a joke because comfort was used twice.
    variant = 'reserved'
    if policy['priority'] == 'repair' or selected['id'] == 'repair':
        variant = 'repair'
    elif state['energy'] < 30:
        variant = 'brief'
    elif policy['warmth'] >= 65 and relationship['comfort'] >= 40:
        variant = 'warm'
    elif selected['id'] == 'playful' and policy['teasing'] >= 30:
        variant = 'playful'
    elif relationship['comfort'] >= 40:
        variant = 'relaxed'
    result = {
        'strategy': selected['id'], 'name': selected['name'], 'outline': selected['outline'],
        'variant': variant, 'reasons': reasons, 'policy': policy,
        'avoid_repeating_expression': selected['id'] in previous[-2:],
        'candidates': [{'strategy': key, 'similarity': round(score, 4)} for key, score in candidates[:3]],
        'catalog_version': catalog['version'],
        'picker_version': 3,
        'user_affect': user_affect or {'status': 'unknown', 'emotions': {}, 'evidence': []},
        'motivation': motivation,
    }
    distress = max((user_affect or {}).get('emotions', {}).get(key, 0)
                   for key in ('sadness', 'anger', 'fear', 'disgust'))
    result['intimacy'] = choose_intimacy_direction(
        adult_policy or {}, intimacy_state or {}, relationship, distress)
    result['response'] = pick_response(result, traits, relationship, state, user_affect)
    return result


class StrategyRouter:
    def __init__(self, client, catalog_path=CATALOG_PATH, cache_dir=None, persona_policy=None):
        self.client, self.catalog = client, load_catalog(catalog_path)
        self.vectors = None
        self.persona_policy = persona_policy or RulePersonaPolicy()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.examples = [(item['id'], text) for item in self.catalog['strategies'] for text in item['examples']]
        self.visual_examples = [(kind, text) for kind, examples in VISUAL_INTENT_EXAMPLES.items() for text in examples]
        self.visual_vectors = None
        self.last_query = None
        # Cache identity includes model path/revision and task prefix, not just text.
        identity = json.dumps(self.examples, ensure_ascii=False) + str(client.model_path) + ':Classification:v1'
        self.cache_key = hashlib.sha256(identity.encode()).hexdigest()

    async def visual_kind(self, text):
        """High-precision semantic fallback after deterministic visual rules."""
        if self.visual_vectors is None:
            self.visual_vectors = await self.client.encode([example for _, example in self.visual_examples])
        query_text = text[:3500]
        query = (await self.client.encode([query_text]))[0]
        self.last_query = (query_text, query)
        if any(len(vector) != len(query) for vector in self.visual_vectors):
            raise RuntimeError('embedding_dimension_mismatch')
        scores = {}
        for (kind, _), vector in zip(self.visual_examples, self.visual_vectors):
            score = sum(a * b for a, b in zip(query, vector))
            scores[kind] = max(scores.get(kind, -1), score)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        winner, score = ranked[0]
        margin = score - ranked[1][1]
        # False positives create expensive work, so abstain unless both the
        # absolute match and separation from alternatives are clear.
        thresholds = {'selfie': .90, 'pov': .90, 'general': .85}
        if winner == 'none' or score < thresholds[winner] or margin < .015:
            return None
        return winner

    async def plan(self, text, store, now):
        started = time.monotonic()
        cache = self.cache_dir / (self.cache_key + '.json') if self.cache_dir else None
        if self.vectors is None and cache and cache.is_file():
            try:
                vectors = json.loads(cache.read_text())
                if (isinstance(vectors, list) and len(vectors) == len(self.examples)
                    and vectors and isinstance(vectors[0], list) and vectors[0]
                    and all(isinstance(v, list) and len(v) == len(vectors[0]) and
                            all(isinstance(x, (int, float)) and math.isfinite(x) for x in v) for v in vectors)):
                    self.vectors = vectors
            except (ValueError, TypeError, OSError):
                pass
        if self.vectors is None:
            self.vectors = await self.client.encode([text for _, text in self.examples])
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.vectors))
                temporary.replace(cache)
        if self.last_query and self.last_query[0] == text[:3500]:
            query = self.last_query[1]
        else:
            query = (await self.client.encode([text[:3500]]))[0]
        self.last_query = None
        if any(len(v) != len(query) for v in self.vectors):
            raise RuntimeError('embedding_dimension_mismatch')
        similarities = {}
        for (key, _), vector in zip(self.examples, self.vectors):
            score = sum(a * b for a, b in zip(query, vector))
            similarities[key] = max(similarities.get(key, -1), score)
        previous = json.loads(store.get('recent_strategies', '[]'))
        character = store.character_profile(now)
        result = select_plan(self.catalog, similarities, character.traits, store.relationship(),
                             character.state_dict(), store.engine.knowledge(), previous, observe_user_emotions(text),
                             store.conversation_state(), character.motivation.need_weights,
                             character.adult_intimacy_dict(), store.adult_policy(), store.intimacy_state())
        snapshot = {
            'message': text, 'character': character.decision_snapshot(), 'persona': character.story_dict(),
            'traits': character.traits, 'relationship': store.relationship(), 'state': character.state_dict(),
            'conversation': store.conversation_state(), 'previous_strategies': previous,
            'adult_policy': store.adult_policy(), 'intimacy_state': store.intimacy_state(),
            'user_affect': result['user_affect'],
            'baseline': result,
        }
        options = policy_options(result, self.catalog, similarities, snapshot['relationship'], snapshot['state'])
        result = await apply_persona_policy(self.persona_policy, snapshot, options, self.catalog)
        # A replaceable policy may choose another allowed strategy. Keep the
        # final purpose/coping record aligned with the decision that reaches the
        # Writer instead of retaining the baseline strategy's motivation.
        result['motivation'] = evaluate_motivation(
            result['strategy'], snapshot['traits'], snapshot['relationship'], snapshot['state'],
            snapshot['conversation'], snapshot['user_affect'], character.motivation.need_weights,
            character.adult_intimacy_dict(), snapshot['adult_policy'], snapshot['intimacy_state'])
        result['intimacy'] = choose_intimacy_direction(
            snapshot['adult_policy'], snapshot['intimacy_state'], snapshot['relationship'],
            max(snapshot['user_affect'].get('emotions', {}).get(key, 0)
                for key in ('sadness', 'anger', 'fear', 'disgust')))
        result['router_seconds'] = round(time.monotonic() - started, 3)
        return result


def expression_contract(plan):
    goals = {
        'support': 'Respond to the specific feeling and offer proportionate support.',
        'playful': 'Continue the specific joke without humiliation.',
        'followup': 'Acknowledge the concrete detail the user shared.',
        'share_self': 'Answer what the user asked about the character; do not redirect the same question back to the user.',
        'repair': 'Address the specific misunderstanding or dissatisfaction first.',
        'direct_answer': 'Answer the current question directly from available facts.',
        'clarify': 'Ask for the one missing detail needed to proceed.',
        'open_response': 'Respond directly to the current message without forcing a special pattern.',
    }
    actions = {
        'answer': 'answer the current question directly', 'acknowledge': 'acknowledge the concrete content',
        'ask': 'ask one relevant unanswered question', 'clarify': 'clarify one necessary missing detail',
        'reflect_feelings': 'tentatively reflect the specific feeling without claiming certainty',
        'affirm': 'give concrete, non-generic support',
        'share_self': 'use supplied character facts; for an unstated preference, express a present choice or uncertainty without claiming it already exists or was prepared',
        'express_feelings': "state the character's own feeling", 'suggest': 'offer an optional suggestion',
        'play': 'continue the joke without humiliation', 'repair': 'repair the specific problem',
        'disagree': 'state a reasoned disagreement about the issue', 'set_boundary': 'state a respectful boundary',
    }
    response = plan['response']
    ordered = [actions[response['primary_action']]]
    if response['secondary_action'] != 'none':
        ordered.append(actions[response['secondary_action']])
    warmth = 'reserved' if response['warmth'] < 35 else 'warm' if response['warmth'] < 70 else 'very warm'
    agency = 'gentle' if response['agency'] < 35 else 'balanced' if response['agency'] < 70 else 'direct'
    purpose, coping = motivation_contract(plan.get('motivation', {}))
    lines = [
        '<response_plan>', f"Purpose: {purpose}.", f"Coping intention: {coping}.",
        f"Communicative goal: {goals[plan['strategy']]}",
        'Actions in order: ' + '; '.join(ordered) + '.',
        f"Delivery: {plan['variant']}; {warmth}; {agency}.",
    ]
    intimacy = plan.get('intimacy', {})
    direction = intimacy.get('direction', 'none')
    intimacy_instructions = {
        'warm_tease': (
            'Intimacy pacing: add one subtle, context-relevant flirtatious undertone. '
            'Do not announce, explain, or ask for procedural permission.'
        ),
        'playful_probe': (
            'Intimacy pacing: naturally reciprocate the user\'s flirtation with one modest step. '
            'Leave room for them to continue or change topic; do not jump in intensity.'
        ),
        'deepen_intimacy': (
            'Intimacy pacing: continue the already receptive adult-intimate tone by only one step '
            'within the configured range. Do not insert a consent questionnaire or discuss system state.'
        ),
        'hold': 'Intimacy pacing: preserve the existing closeness without increasing intensity this turn.',
        'cool_down': (
            'Intimacy pacing: immediately remove erotic escalation, respect the stop or topic shift, '
            'and respond naturally without guilt, pressure, or negotiation.'
        ),
    }
    if direction in intimacy_instructions:
        lines.append(intimacy_instructions[direction])
    if plan['strategy'] == 'playful':
        teasing = plan['policy']['teasing']
        lines.append('Teasing: light.' if teasing < 50 else 'Teasing: pronounced but never degrading.')
    if plan['strategy'] == 'share_self':
        disclosure = plan['policy']['personal_disclosure']
        lines.append('Disclosure: guarded.' if disclosure < 35 else 'Disclosure: personal but bounded.' if disclosure < 70 else 'Disclosure: open when relevant.')
    if plan.get('avoid_repeating_expression'):
        lines.append('Use fresh wording; do not repeat the previous reply pattern or catchphrase.')
    lines.extend([
        'This is remote text chat: do not claim physical proximity or seeing the user unless the current dialogue explicitly establishes a shared fictional scene.',
        'Use only supplied facts. Output only the final character reply.', '</response_plan>',
    ])
    if response['secondary_action'] == 'none' and response['primary_action'] not in {'ask', 'clarify'}:
        lines.insert(-1, 'Do not ask a question or end with a question.')
    return '\n'.join(lines)


def apply_expression_plan(messages, plan):
    """Attach trusted control next to the final user turn without persisting it.

    Some small local writers underweight a long leading system prompt. Keeping a
    concise copy adjacent to the generation point improves adherence while the
    full contract remains authoritative in the system message.
    """
    copied = [dict(message) for message in messages]
    for index in range(len(copied) - 1, -1, -1):
        if copied[index].get('role') == 'user':
            copied[index]['content'] = copied[index]['content'] + '\n\n' + expression_contract(plan)
            return copied
    return copied


def response_token_budget(plan, configured):
    """Bound rambling for short social acts while preserving factual answers."""
    response = plan.get('response', {})
    primary = response.get('primary_action')
    if plan.get('variant') == 'brief':
        return min(configured, 192)
    if primary in {'answer', 'clarify', 'suggest'}:
        return configured
    if response.get('secondary_action') == 'none':
        return min(configured, 128)
    return min(configured, 160)
