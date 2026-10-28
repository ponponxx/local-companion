"""Transport-independent, evidence-backed decision core for the workbench.

All numbers in RULES are versioned product defaults, not psychological scales.
No database, Telegram, embedding service, or mutable provider state is used here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from typing import Any

from .character_profile import DEFAULT_CHARACTER_TRAITS, DEFAULT_NEED_WEIGHTS
from .adult_intimacy import ADULT_TRAIT_DEFAULTS, ADULT_RELATIONSHIP_DEFAULTS
from .emotions import EMOTION_LABELS, emotion_mood
from .activity_affordances import ACTIVITY_RULES, describe_activity

VERSION = 'pyramid-v1.1.0'
RULES = {
    'version': VERSION, 'status': 'engineering_defaults_unvalidated',
    'activity_rules': ACTIVITY_RULES,
    'short_deltas': [0, 2, 5, 10], 'relationship_deltas': [0, .2, .5, 1],
    'load_levels': [0, 5, 15, 30], 'load_cap': 80,
    'emotion_tau_hours': 2, 'stress_tau_hours': 4, 'tension_tau_hours': 2,
    'energy_rates': {'sleep': 12, 'busy': -3, 'free': 2},
    'scene_idle_minutes': 10, 'scene_notice_minutes': 10,
    'history_turns': 6, 'history_minutes': 60, 'heat_levels': [0, 20, 40, 70],
    'tension_release_fraction': .5,
    'references': [
        {'name': 'EMA (Marsella & Gratch, 2009)', 'url': 'https://people.ict.usc.edu/~gratch/CSCI534/Readings/COGSYS-RS-EMOTION-2008-6.pdf'},
        {'name': 'Ryan & Deci (2000)', 'url': 'https://selfdeterminationtheory.org/SDT/documents/2000_RyanDeci_SDT.pdf'},
        {'name': 'Laurenceau et al. (1998)', 'url': 'https://affective-science.org/pubs/1998/LaurenFBPl1998.pdf'},
        {'name': 'Bancroft & Janssen (2000)', 'url': 'https://pubmed.ncbi.nlm.nih.gov/10880822/'},
        {'name': 'Basson (2001)', 'url': 'https://pubmed.ncbi.nlm.nih.gov/11224952/'},
    ],
}
RELATIONSHIP_DEFAULTS = {
    'familiarity': 5, 'trust': 15, 'affection': 15, 'comfort': 10, 'respect': 55,
    'intimacy': 5, 'tension': 0, 'initiative': 20, **ADULT_RELATIONSHIP_DEFAULTS,
}
KINDS = ['move', 'appearance', 'pose', 'expression', 'invite', 'accept', 'refuse',
         'start', 'complete', 'cancel', 'question', 'share', 'support', 'disclosure',
         'responsive', 'promise', 'fulfilled', 'misunderstanding', 'repair', 'violation',
         'flirt', 'intimate', 'stop', 'pause', 'stressor', 'resolve', 'emotion', 'image_request',
         'boundary_respected', 'care_benefit', 'answer', 'acknowledge', 'clarify',
         'profile_add', 'profile_update', 'profile_delete']
STAGES = ['proposed', 'accepted', 'rejected', 'started', 'completed', 'cancelled', 'observed']
HAIR = {'盤髮': '盤髮', '盤發': '盤髮', '盤起': '盤髮', 'bun': '盤髮', 'updo': '盤髮',
        '披髮': '披髮', '披發': '披髮', '長髮披下': '披髮', 'loose': '披髮',
        '馬尾': '馬尾', 'ponytail': '馬尾', '辮子': '辮子', 'braid': '辮子',
        '雙馬尾': '雙馬尾', '半盤髮': '半盤髮'}
CLOTHING_SLOTS = ['outerwear', 'top', 'bottom', 'dress', 'underwear', 'socks', 'shoes', 'accessories']
FIELDS = ['location', 'hair', 'pose', 'expression', 'activity', 'availability', *CLOTHING_SLOTS, *EMOTION_LABELS]
ACTIONS = {
    'answer': {'competence': 3}, 'clarify': {'curiosity': 3, 'competence': 2},
    'support': {'relatedness': 3}, 'share_self': {'relatedness': 2},
    'accept': {'relatedness': 2, 'homeostasis': 1},
    'conditional_accept': {'autonomy': 2, 'relatedness': 2, 'homeostasis': 1},
    'refuse': {'autonomy': 3, 'homeostasis': 2},
    'suggest': {'competence': 2, 'autonomy': 1, 'relatedness': 1},
    'repair': {'relatedness': 3, 'autonomy': 1},
    'invite': {'relatedness': 2, 'erotic_connection': 3},
    'defer': {'homeostasis': 3, 'autonomy': 2}, 'acknowledge': {'relatedness': 1},
    'ask': {'relatedness': 2,'curiosity':3},
}
ACTION_LABELS = {'answer': '回答', 'clarify': '澄清', 'support': '支持', 'share_self': '分享自身',
                 'accept': '接受', 'conditional_accept': '附條件接受', 'refuse': '婉拒',
                 'suggest': '協商替代方案', 'repair': '修復',
                 'invite': '提出邀請', 'defer': '暫緩', 'acknowledge': '承接','ask':'詢問近況'}
# Explicit, inspectable trait preference matrix. Unknown/unused inputs are disclosed.
TRAIT_PREFS = {
    'answer': [('conscientiousness', 1)], 'clarify': [('openness', 1), ('conscientiousness', 1)],
    'support': [('agreeableness', 1)], 'share_self': [('extraversion', 1), ('expressiveness', 1)],
    'accept': [('agreeableness', 1), ('impulsiveness', 1)],
    'conditional_accept': [('assertiveness', 1), ('conscientiousness', 1)],
    'refuse': [('assertiveness', 1)], 'suggest': [('openness', 1), ('agreeableness', 1)],
    'repair': [('conscientiousness', 1), ('agreeableness', 1)],
    'invite': [('extraversion', 1), ('impulsiveness', 1)],
    'defer': [('conscientiousness', 1)], 'acknowledge': [('emotional_stability', 1)],
    'ask':[('openness',1),('agreeableness',1)],
}


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def date(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('時間必須包含時區')
    return result


def bounded(value, low=0, high=100):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('數值必須是有限數字')
    return round(max(low, min(high, value)), 3)


def band(value):
    return 0 if value < 25 else 1 if value < 50 else 2 if value < 75 else 3


@dataclass
class InteractionEvent:
    kind: str
    actor: str
    target: str
    stage: str
    field: str
    value: str
    evidence: str
    strength: int
    ref: str = ''
    source: str = 'user'
    id: str = ''
    timestamp: str = ''
    turn_id: str = ''


@dataclass
class StateSnapshot:
    payload: dict


@dataclass
class TurnInput:
    text: str
    turn_id: str
    generate_reply: bool = False
    at: str = ''


@dataclass
class Appraisal:
    needs: dict
    evidence: list


@dataclass
class ActionDecision:
    payload: dict


@dataclass
class DecisionTrace:
    nodes: list = field(default_factory=list)

    def add(self, layer, rule, inputs, output, reason, **extra):
        self.nodes.append({'layer': layer, 'rule': rule, 'version': VERSION,
                           'inputs': deepcopy(inputs), 'output': deepcopy(output), 'reason': reason, **extra})
        from .diagnostics import event
        event('trace',self.nodes[-1])


@dataclass
class TurnResult:
    before: dict
    after: dict
    decision: dict
    trace: list
    reply: str = ''


def initial_state(now=None):
    stamp = now or iso_now()
    return {
        'version': VERSION, 'now': stamp, 'updated_at': stamp, 'last_user_at': stamp,
        'control_mode': 'autonomous', 'language': 'CH',
        'story': {'name': '測試角色', 'background': '', 'personality': '', 'tone': ''},
        'user_profile': [], 'traits': deepcopy(DEFAULT_CHARACTER_TRAITS),
        'need_weights': deepcopy(DEFAULT_NEED_WEIGHTS), 'adult_traits': deepcopy(ADULT_TRAIT_DEFAULTS),
        'adult_policy': {'mode': 'off', 'agent_may_initiate': True},
        'relationship': deepcopy(RELATIONSHIP_DEFAULTS),
        'emotions': dict.fromkeys(EMOTION_LABELS, 0), 'mood': '平靜', 'energy': 70, 'stress': 20,
        'acute_stress': 20, 'loads': {}, 'availability': 'free', 'activity': '',
        'scene': {'location': '', 'user_location': '', 'character_location': '', 'positions': {}, 'status': 'none', 'pending': None},
        'appearance': {'body': '', 'hair': '', 'clothing': {}, 'accessories': ''},
        'user_appearance': {'hair': '', 'clothing': {}},
        'presentation': {}, 'invitations': {}, 'activities': {}, 'issues': {}, 'promises': {},
        'intimacy': {'heat': 0, 'tension': 0, 'desire': 0, 'inhibition': 0,
                     'mutuality': 0, 'momentum': 0, 'receptivity': 'unknown'},
        'history': [], 'events': [], 'applied': [], 'notices': [], 'schedule': [], 'scene_history': [],
        'legacy_summary': '', 'summary': '', 'summary_through': 0,
        'memory': {'version':'observations-v1', 'notes':[], 'selection':{}, 'previous_summary':''},
    }


def validate_state(state):
    # State editing is explicit sandbox administration, never model output.
    if state.get('control_mode') not in {'director', 'autonomous'}:
        raise ValueError('故事模式必須為 director 或 autonomous')
    if state.get('language') not in {'CH', 'EN', 'JP'}:
        raise ValueError('語言必須為 CH、EN 或 JP')
    if state.get('adult_policy', {}).get('mode') not in {'off', 'flirt', 'intimate'}:
        raise ValueError('成人模式無效')
    if state.get('availability') not in {'free', 'busy', 'sleep'}:
        raise ValueError('忙閒狀態無效')
    for group in ('traits', 'relationship', 'emotions', 'adult_traits'):
        for key, value in state[group].items():
            state[group][key] = bounded(value)
    for key, value in state['need_weights'].items():
        state['need_weights'][key] = bounded(value, .25, 2)
    for key in ('energy', 'acute_stress'):
        state[key] = bounded(state[key])
    for key in ('heat', 'tension'):
        state['intimacy'][key] = bounded(state['intimacy'][key])
    date(state['now'])
    for entry in state.get('schedule', []):
        date(entry['at'])
        if not isinstance(entry.get('location'), str) or len(entry['location']) > 180:
            raise ValueError('行程地點無效')
        if entry.get('availability', 'free') not in {'free', 'busy', 'sleep'}:
            raise ValueError('行程忙閒狀態無效')
    state['mood'] = emotion_mood(state['emotions'])
    return state


class PyramidCore:
    def __init__(self, rules=None):
        self.rules = deepcopy(rules or RULES)

    def advance(self, state, to, trace):
        from .scene_activity import reconcile_arrival
        reconcile_arrival(state,trace)
        start, end = date(state['now']), date(to)
        if end < start:
            raise ValueError('測試時間不可倒退；請重設沙盒')
        from .visual_state import reset_morning_appearance
        reset_morning_appearance(state, end)
        # Integrate activity rates across notification/commit boundaries, even
        # when a user jumps an hour with one click.
        self._scene_clock(state, trace)
        while date(state['now']) < end:
            now = date(state['now'])
            boundaries = [end]
            pending = state['scene'].get('pending')
            if pending and pending.get('deadline') and now < date(pending['deadline']) <= end:
                boundaries.append(date(pending['deadline']))
            idle_at = date(state['last_user_at']) + timedelta(minutes=self.rules['scene_idle_minutes'])
            if not pending:
                for item in state['schedule']:
                    if item['location'] == state['scene']['location']: continue
                    notification_at = max(date(item['at']), idle_at)
                    if now < notification_at <= end: boundaries.append(notification_at)
            self._advance_segment(state, min(boundaries).isoformat(), trace)
        if start == end:
            self._advance_segment(state, to, trace)

    def _advance_segment(self, state, to, trace):
        start, end = date(state['now']), date(to)
        hours = (end - start).total_seconds() / 3600
        before = {k: deepcopy(state[k]) for k in ('emotions', 'energy', 'stress', 'acute_stress', 'intimacy')}
        state['emotions'] = {k: round(v * math.exp(-hours / self.rules['emotion_tau_hours']), 3)
                             for k, v in state['emotions'].items()}
        state['energy'] = bounded(state['energy'] + hours * self.rules['energy_rates'][state['availability']])
        state['acute_stress'] = bounded(state['acute_stress'] * math.exp(-hours / self.rules['stress_tau_hours']))
        self._stress(state)
        state['intimacy']['tension'] = bounded(state['intimacy']['tension'] * math.exp(-hours / self.rules['tension_tau_hours']))
        state['now'] = state['updated_at'] = to
        state['mood'] = emotion_mood(state['emotions'])
        self._scene_clock(state, trace)
        self.derive_intimacy(state, trace)
        trace.add(1, 'time.advance', {'from': start.isoformat(), 'to': to, 'before': before,
                  'rules': {k: self.rules[k] for k in ('emotion_tau_hours', 'stress_tau_hours', 'energy_rates', 'tension_tau_hours')}},
                  {k: state[k] for k in before}, '時間降低短期反應；不刪除未解決事件、不更改長期關係')

    def _stress(self, state):
        floor = min(self.rules['load_cap'], sum(v['value'] for v in state['loads'].values()))
        state['stress'] = bounded(max(floor, state['acute_stress']))

    def _commit_schedule(self, state, item, trace, *, initial=False, silent=False):
        from .memory_metadata import scene_snapshot, record_scene
        before=scene_snapshot(state)
        scene=state['scene']
        from .scene_background import same_location
        moved=not same_location(state,scene.get('character_location') or scene['location'],item['location'])
        from .journeys import leave_schedule
        leave_schedule(state)
        scene.update(location=item['location'], character_location=item['location'], status='active', pending=None)
        # Autonomous character movement never moves the user.
        if moved:scene['positions']={}
        from .scene_activity import arrival_activity
        state['activity']=arrival_activity(item)
        state['availability']=item.get('availability','free')
        state['presentation']={}
        from .scene_appearance import restyle_arrival
        restyle_arrival(state,item)
        state['life_current']=deepcopy(item)
        record_scene(state,'initialized' if initial else ('arrived' if moved else 'activity_started'),before,
                     'daily_schedule_initial' if initial else 'daily_schedule_silent' if silent else 'daily_schedule')
        if moved and not initial:
            idle_minutes=max(0,int((date(state['now'])-date(state['last_user_at'])).total_seconds()/60))
            state['_scene_handoff']={'origin':before.get('character_location') or before.get('location',''),
                'destination':item['location'],'at':state['now'],'idle_minutes':idle_minutes,
                'after_history_index':len(state['history']),
                'notice_status':'silent' if silent else item.get('delivery','simulated_delivered'),
                'notice_at':item.get('sent_at','')}
            trace.add(1,'scene.handoff',{},deepcopy(state['_scene_handoff']),
                      '保存角色自行換場的接續事實；未通知不可宣稱已告知，不推定使用者同行。')
        trace.add(1,'scene.initialize' if initial else 'scene.commit_silent' if silent else 'scene.commit_notice',item,scene,
                  '依今日有效日程建立首次現況；不補造先前行蹤、不移動使用者' if initial else
                  '依日程在背景切換現況；沒有傳訊或偽造通知收據，不移動使用者' if silent else
                  '通知送達後十分鐘，角色換場；不移動使用者；日程造型在活動開始時生效')

    def acknowledge_scene_notice(self, state, notice_id, delivered_at, trace):
        pending=state['scene'].get('pending')
        if not pending or pending.get('notice_id')!=notice_id or pending.get('delivery')!='queued':return False
        from .memory_metadata import scene_snapshot, record_scene
        before=scene_snapshot(state)
        pending.update(delivery='delivered',sent_at=delivered_at,
                       deadline=(date(delivered_at)+timedelta(minutes=self.rules['scene_notice_minutes'])).isoformat())
        state['scene']['status']='moving'
        state['activity']='前往'+pending['location']
        for notice in state['notices']:
            if notice.get('notice_id')==notice_id:notice.update(delivery='delivered',at=delivered_at)
        record_scene(state,'announced',before,'telegram_receipt',delivered_at)
        trace.add(1,'scene.notice_delivered',{'notice_id':notice_id},pending,'由傳輸確認送達時間開始倒數；排隊時間不算')
        return True

    def _scene_clock(self, state, trace):
        if state.get('scene_clock_enabled',True) is False:return
        now,scene=date(state['now']),state['scene']
        due=[x for x in state['schedule'] if date(x['at']).astimezone(now.tzinfo).date()==now.date() and date(x['at'])<=now]
        upcoming=max(due,key=lambda x:date(x['at'])) if due else None
        pending=scene.get('pending')
        from .scene_background import same_location
        if pending and same_location(state,scene.get('character_location') or scene.get('location'),pending['location']):
            trace.add(1,'scene.same_place_cancel',{}, {'destination':pending['location']},'目前已在同一場景；取消不必要的出發通知／抵達倒數，不偽造換場。')
            scene['pending']=None;pending=None
            if scene.get('status')=='moving':
                scene['status']='active'
                from .scene_activity import is_travel
                if is_travel(state.get('activity','')):state['activity']=''
        if pending and pending.get('deadline') and now>=date(pending['deadline']):
            self._commit_schedule(state,pending,trace)
        if (upcoming and not scene.get('pending') and scene.get('status')=='none'
                and (scene.get('character_location') or scene.get('location','')) in {'','未確認','unknown'}):
            self._commit_schedule(state,upcoming,trace,initial=True)
            return
        idle=(now-date(state['last_user_at'])).total_seconds()>=self.rules['scene_idle_minutes']*60
        if not upcoming or not idle or scene.get('pending'):return
        if same_location(state,scene.get('character_location') or scene['location'],upcoming['location']):
            fields=('at','activity','availability','clothing','accessories','arrival_activity','plan_source')
            if any(state.get('life_current',{}).get(k)!=upcoming.get(k) for k in fields):
                self._commit_schedule(state,upcoming,trace,silent=state.get('scene_notice_delivery')=='external' and not state.get('scene_notifications_enabled',False))
            return
        external=state.get('scene_notice_delivery')=='external'
        notice_id=hashlib.sha256((upcoming['at']+':'+upcoming['location']+':'+state['last_user_at']).encode()).hexdigest()[:20]
        if external and notice_id in state.get('scene_notice_blocked',[]):return
        if external and not state.get('scene_notifications_enabled',False):
            # Notification permission is not permission to live. The ordinary
            # idle boundary still protects an ongoing conversation. No receipt
            # or fake delivery countdown is created for a silent transition.
            if now<date(state.get('clock',{}).get('now') or state['now']):return
            self._commit_schedule(state,upcoming,trace,silent=True)
            return
        pending={**deepcopy(upcoming),'notice_id':notice_id,'delivery':'queued' if external else 'simulated_delivered'}
        pending['departure_context']={'origin':scene.get('character_location') or scene.get('location',''),
            'idle_minutes':max(0,int((now-date(state['last_user_at'])).total_seconds()/60))}
        if not external:
            pending.update(sent_at=state['now'],deadline=(now+timedelta(minutes=self.rules['scene_notice_minutes'])).isoformat())
        from .memory_metadata import scene_snapshot, record_scene
        before=scene_snapshot(state)
        scene['pending']=pending
        if not external:
            scene['status']='moving';state['activity']='前往'+upcoming['location']
        record_scene(state,'preparing',before,'daily_schedule')
        notice={'text':f"我準備去{upcoming['location']}了。",'at':state['now'],
                'notice_id':notice_id,'delivery':pending['delivery']}
        state['notices'].append(notice)
        trace.add(1,'scene.notify',upcoming,pending,'等待真實傳輸確認；未送達不得換場' if external else '沙盒模擬送達，等待十分鐘')

    def user_arrived(self, state, trace, at=None):
        pending=state['scene'].get('pending')
        if pending and pending.get('delivery') not in {'delivered','simulated_delivered'}:
            from .memory_metadata import scene_snapshot, record_scene
            before=scene_snapshot(state)
            trace.add(1, 'scene.cancel_wait', state['scene']['pending'], None, '使用者在等待期間回應，取消待切換；下次閒置重新評估')
            state['scene']['pending'] = None
            record_scene(state,'cancelled',before,'user_arrived',at)
        state['last_user_at'] = at or state['now']

    def apply(self, state, proposals, source, text, turn_id, trace, at=None):
        accepted, rejected = [], []
        before = deepcopy(state)
        for ordinal, raw in enumerate(proposals[:24]):
            try:
                event = InteractionEvent(**{key: raw[key] for key in (
                    'kind', 'actor', 'target', 'stage', 'field', 'value', 'evidence', 'strength')},
                    ref=raw.get('ref', ''), source=source, turn_id=turn_id, timestamp=at or state['now'])
                if event.kind=='move':
                    from .journeys import resolve
                    movement_error=resolve(state,event)
                    if movement_error:
                        rejected.append({'proposal':raw,'reason':movement_error});continue
                identity = (turn_id, source, event.kind, event.actor, event.target, event.field,
                            event.value, event.evidence, event.ref)
                event.id = hashlib.sha256(repr(identity).encode()).hexdigest()[:20]
                reason = self._validate(state, event, text)
                edited=state.get('_clothing_edits',{}).get(event.field)
                if event.kind=='appearance' and event.actor=='character' and edited and date(event.timestamp)<date(edited):
                    reason='這份舊回文早於手動穿著設定，不覆寫較新的現況'
                if event.kind.startswith('profile_') and sum(e['kind'].startswith('profile_') for e in accepted) >= 2:
                    reason = '每輪最多兩筆 Profile 操作'
                if event.id in state['applied']:
                    reason = '同一來源事件已計分'
                if reason:
                    rejected.append({'proposal': raw, 'reason': reason})
                    continue
                staged = deepcopy(state)
                self._reduce(staged, event)
                if event.kind=='move' and staged['scene']!=state['scene']:
                    from .memory_metadata import scene_snapshot, record_scene
                    phase='departed' if event.field=='departure' else 'position_changed' if event.field in {'relative_position','distance'} else 'moved'
                    scene_actor='character' if (staged['scene'].get('character_location')!=state['scene'].get('character_location')
                        or staged['scene'].get('journeys',{}).get('character')!=state['scene'].get('journeys',{}).get('character')) else event.actor
                    record_scene(staged,phase,scene_snapshot(state),event.source,event.timestamp,scene_actor)
                state.clear()
                state.update(staged)
                state['events'].append(asdict(event))
                state['applied'].append(event.id)
                accepted.append(asdict(event))
            except (KeyError, TypeError, ValueError) as exc:
                rejected.append({'proposal': raw, 'reason': '事件格式或轉移無效：' + str(exc)[:160]})
        old_location=before['scene'].get('character_location') or before['scene'].get('location')
        new_location=state['scene'].get('character_location') or state['scene'].get('location')
        if new_location!=old_location and not state['scene'].get('pending'):
            from .scene_appearance import restyle_arrival
            protected={e['field'] for e in accepted if e['kind']=='appearance' and e['actor']=='character'}
            protected.update(k for k,t in state.get('_clothing_edits',{}).items() if date(at or state['now'])<date(t))
            result=restyle_arrival(state,protected=protected)
            trace.add(2,'appearance.arrival',{},result,'抵達後套用目的地穿搭；同輪明確外觀事件優先')
        self._stress(state)
        state['mood'] = emotion_mood(state['emotions'])
        self.derive_intimacy(state, trace)
        changed = {k: {'before': before.get(k), 'after': deepcopy(state[k])} for k in state if state[k] != before.get(k) and k not in {'events', 'applied'}}
        trace.add(2 if source == 'user' else 5, 'events.validate_reduce',
                  {'source': source, 'mode': state['control_mode'], 'proposals': proposals},
                  {'accepted': accepted, 'rejected': rejected, 'changes': changed},
                  '逐字來源、主體權限、型別與狀態轉移檢查；引文存在不代表模型語義必然正確')
        return accepted

    def _validate(self, state, e, text):
        if e.kind not in KINDS or e.actor not in {'user', 'character'} or e.target not in {'user', 'character', 'scene', 'none'} or e.stage not in STAGES:
            return '不允許的種類、人物或階段'
        if type(e.strength) is not int or not 0 <= e.strength <= 3:
            return '影響等級必須為 0–3'
        if any(not isinstance(v, str) or len(v) > 400 for v in (e.field, e.value, e.evidence, e.ref)):
            return '欄位必須是有限長字串'
        if not e.evidence.strip() or e.evidence not in text:
            return '證據不在對應說話者原文'
        if e.source == 'assistant' and e.actor != 'character':
            return '角色回文無權建立使用者行動'
        if (e.source == 'user' and e.actor == 'character' and state['control_mode'] == 'autonomous'
                and e.kind not in {'move', 'appearance', 'pose', 'expression'}):
            return '自主模式：使用者不能替角色建立意願或內心狀態'
        if e.kind in {'move', 'appearance', 'pose', 'expression', 'start', 'complete', 'fulfilled', 'stressor', 'resolve', 'emotion'} and e.stage not in {'started', 'completed', 'observed'}:
            return '提議或假設不等於已發生'
        if e.kind in {'accept', 'refuse', 'start', 'complete', 'cancel'}:
            invitation = state['invitations'].get(e.ref)
            if not invitation:
                return '找不到對應邀請'
            if e.kind in {'accept', 'refuse'} and (invitation['target'] != e.actor or invitation['status'] != 'proposed'):
                return '只有受邀者可回應尚待處理的邀請'
            if e.kind == 'start' and invitation['status'] != 'accepted':
                return '尚未接受，不能開始'
            if e.kind == 'complete' and invitation['status'] != 'started':
                return '尚未開始，不能完成'
            if (e.kind in {'accept', 'start'} and invitation.get('purpose') == 'intimacy'
                    and state['adult_policy']['mode'] == 'off'):
                return '故事敘述權不能修改成人互動範圍'
        if e.kind == 'invite' and e.field not in {'', 'care', 'task', 'social', 'intimacy', 'other'}:
            return '邀請用途分類無效'
        if e.kind in {'fulfilled', 'repair', 'resolve', 'responsive'}:
            collection = {'fulfilled': 'promises', 'repair': 'issues', 'resolve': 'loads', 'responsive': 'events'}[e.kind]
            if collection == 'events':
                if not any(v['id'] == e.ref and v['kind'] == 'disclosure' and v['actor'] != e.actor for v in state['events']):
                    return '承接需對應另一方的揭露事件'
            elif e.ref not in state[collection]:
                return '缺少可對應的先前事件'
        if e.kind == 'fulfilled' and state['promises'][e.ref]['actor'] != e.actor:
            return '不能替另一方履行承諾'
        if e.kind == 'boundary_respected':
            invite = state['invitations'].get(e.ref)
            if not invite or invite['status'] != 'rejected' or invite['actor'] != e.actor:
                return '尊重拒絕需要另一方先前拒絕的邀請'
        if e.kind == 'care_benefit':
            if e.actor != 'character' or e.ref not in state['activities']:
                return '照顧效果需要角色確認與已開始的相關活動'
        if e.kind == 'appearance':
            if e.field not in ['hair', *CLOTHING_SLOTS]:
                return '不是有效外觀欄位'
            if e.field == 'hair' and e.value not in HAIR:
                return '髮型未收錄；保留原狀，請用設定欄確認自訂造型'
        if e.kind == 'move':
            recent=[h for h in state.get('scene_history',[]) if h['phase'] in {'arrived','moved','departed','closed'}]
            if recent and date(e.timestamp)<max(date(h['at']) for h in recent):
                return '過期來源的場景事件，不覆寫較新的已確認場景'
        if e.kind == 'move' and e.field not in {'location', 'departure', 'relative_position', 'distance'}:
            return '移動須區分地點與相對位置'
        if e.kind.startswith('profile_'):
            if e.source != 'user' or e.actor != 'user' or e.target != 'user':
                return 'Profile 只能取自使用者對自己的明確描述'
            if e.field not in {'name', 'interest', 'habit', 'preference', 'goal', 'boundary'}:
                return '非穩定使用者資訊類別'
            if e.kind != 'profile_delete' and (not e.value or e.value not in e.evidence):
                return 'Profile 內容需在原文引文中'
            if e.kind != 'profile_add' and not any(str(v.get('id')) == e.ref for v in state['user_profile']):
                return 'Profile 目標 ID 不存在'
            if re.search(r'密碼|password|token|api.?key|認證|信用卡|銀行帳號', e.evidence, re.I):
                return '不把憑證或敏感帳號學入 Profile'
        if e.kind == 'emotion' and (e.field not in EMOTION_LABELS or e.value not in {'increase', 'decrease'}):
            return '情緒欄位或方向無效'
        if e.kind in {'flirt', 'intimate'} and state['adult_policy']['mode'] == 'off':
            return '成人互動範圍已關閉'
        return ''

    def _reduce(self, state, e):
        short, long = self.rules['short_deltas'][e.strength], self.rules['relationship_deltas'][e.strength]
        rel = state['relationship']
        def change(key, delta):
            rel[key] = bounded(rel[key] + delta)
        if e.kind == 'move':
            if e.field in {'relative_position','distance'}:
                state['scene'].setdefault('positions',{})[e.actor]={'relation':e.value,'target':e.target,'source':e.id}
                state['presentation']={}
            else:
                from .journeys import reduce
                reduce(state,e)
        elif e.kind == 'appearance':
            appearance = state['appearance'] if e.actor == 'character' else state['user_appearance']
            if e.field == 'hair': appearance['hair'] = HAIR[e.value]
            else:
                from .visual_state import is_removed
                appearance['clothing'][e.field] = {
                    'description': e.value, 'worn': not is_removed(e.value),
                    'source': e.id}
        elif e.kind in {'pose', 'expression'} and e.actor == 'character':
            state['presentation'][e.kind] = {'value': e.value, 'source': e.id}
        elif e.kind == 'invite':
            linked = state['invitations'].get(e.ref)
            if not linked or linked['status'] not in {'proposed', 'accepted', 'started'}:
                state['invitations'][e.id] = {'actor': e.actor, 'target': e.target, 'action': e.value,
                                            'purpose': e.field or 'other',
                                            'status': 'proposed', 'conditions': '', 'source': e.id}
        elif e.kind in {'accept', 'refuse', 'start', 'complete', 'cancel'}:
            invitation = state['invitations'][e.ref]
            invitation['status'] = {'accept': 'accepted', 'refuse': 'rejected', 'start': 'started',
                                     'complete': 'completed', 'cancel': 'cancelled'}[e.kind]
            if e.kind == 'accept': invitation['conditions'] = e.value
            if e.kind == 'start': state['activities'][e.ref] = deepcopy(invitation)
            if e.kind in {'complete', 'cancel'}:
                state['activities'].pop(e.ref, None)
                state['presentation'] = {}
        elif e.kind == 'promise':
            state['promises'][e.id] = {'text': e.value, 'actor': e.actor, 'fulfilled': False}
        elif e.kind == 'fulfilled':
            if not state['promises'][e.ref]['fulfilled']:
                state['promises'][e.ref]['fulfilled'] = True
                if e.actor == 'user': change('trust', long); change('respect', long)
        elif e.kind == 'misunderstanding':
            state['issues'][e.id] = {'text': e.value, 'resolved': False}
            change('tension', long)
        elif e.kind == 'repair':
            if not state['issues'][e.ref]['resolved']:
                state['issues'][e.ref]['resolved'] = True
                change('tension', -long); change('comfort', long)
        elif e.kind == 'violation':
            if e.actor == 'user':
                change('trust', -long); change('comfort', -long); change('respect', -long); change('tension', long)
                state['emotions']['anger'] = bounded(state['emotions']['anger'] + short)
        elif e.kind == 'responsive' and e.actor == 'user':
            change('intimacy', long); change('comfort', long)
        elif e.kind == 'boundary_respected' and e.actor == 'user':
            change('comfort', long); change('intimate_safety', long)
        elif e.kind == 'care_benefit':
            state['acute_stress'] = bounded(state['acute_stress'] - short)
            state['emotions']['joy'] = bounded(state['emotions']['joy'] + short)
            change('comfort', long)
        elif e.kind.startswith('profile_'):
            facts = state['user_profile']
            previous = next((v for v in facts if str(v.get('id')) == e.ref), None)
            if e.kind == 'profile_delete': facts.remove(previous)
            elif e.kind == 'profile_update':
                previous.update(category=e.field, value=e.value, evidence=e.evidence, source=e.id,
                                confidence=max(.65, previous.get('confidence', .55)))
            else:
                duplicate = next((v for v in facts if v.get('category') == e.field and v.get('value') == e.value), None)
                if duplicate: duplicate['confidence'] = min(.95, duplicate.get('confidence', .55) + .1)
                elif len(facts) < 50:
                    facts.append({'id': e.id, 'category': e.field, 'value': e.value, 'confidence': .55,
                                  'evidence': e.evidence, 'source': e.id})
                    change('familiarity', self.rules['relationship_deltas'][1])
        elif e.kind == 'support' and e.actor == 'user':
            # Helpful intent alone is not successful care. A response event must confirm benefit.
            pass
        elif e.kind == 'emotion' and e.actor == 'character':
            sign = 1 if e.value == 'increase' else -1
            state['emotions'][e.field] = bounded(state['emotions'][e.field] + sign * short)
        elif e.kind == 'stressor' and e.actor == 'character':
            state['loads'][e.id] = {'text': e.value, 'value': self.rules['load_levels'][e.strength]}
            state['acute_stress'] = bounded(state['acute_stress'] + short)
        elif e.kind == 'resolve':
            state['loads'].pop(e.ref)
        elif e.kind in {'stop', 'pause'}:
            state['intimacy'].update(tension=0, receptivity='stop' if e.kind == 'stop' else 'pause')
            if e.kind == 'stop': state['intimacy']['heat'] = 0
        elif e.kind in {'flirt', 'intimate'}:
            requested_heat = self.rules['heat_levels'][min(3, e.strength)]
            cap = 40 if state['adult_policy']['mode'] == 'flirt' else 100
            # Only mutual evidence from the other participant in a recent turn can confirm an upgrade.
            recent = self._recent(state)
            last_stop = max((i for i, v in enumerate(recent) if v['kind'] in {'stop', 'pause'}), default=-1)
            recent = recent[last_stop + 1:]
            partner = any(v['kind'] in {'flirt', 'intimate'} and v['actor'] != e.actor for v in recent)
            old = state['intimacy']['heat']
            if partner and min(cap, requested_heat) > old:
                state['intimacy']['heat'] = min(cap, requested_heat)
                state['intimacy']['tension'] *= self.rules['tension_release_fraction']
            else:
                state['intimacy']['tension'] = bounded(state['intimacy']['tension'] + short)
            state['intimacy']['receptivity'] = 'receptive' if partner else 'proposed'

    def _recent(self, state):
        cutoff = date(state['now']) - timedelta(minutes=self.rules['history_minutes'])
        recent = [e for e in state['events'] if date(e['timestamp']) >= cutoff]
        turns = list(dict.fromkeys(e['turn_id'] for e in recent))[-self.rules['history_turns']:]
        return [e for e in recent if e['turn_id'] in turns]

    def derive_intimacy(self, state, trace):
        recent = self._recent(state)
        relevant = [e for e in recent if e['kind'] in {'flirt', 'intimate', 'stop', 'pause'}]
        last_break = max((i for i, e in enumerate(relevant) if e['kind'] in {'stop', 'pause'}), default=-1)
        relevant = relevant[last_break + 1:]
        grouped = {}
        for e in relevant: grouped.setdefault(e['turn_id'], set()).add(e['actor'])
        mutual = [len(v) == 2 for v in grouped.values()]
        intimacy = state['intimacy']
        intimacy['mutuality'] = round(100 * sum(mutual) / len(mutual), 2) if mutual else 0
        streak = 0
        for value in reversed(mutual):
            if not value: break
            streak += 1
        intimacy['momentum'] = round(streak / self.rules['history_turns'] * 100, 2)
        traits, rel = state['adult_traits'], state['relationship']
        contextual = max((e['strength'] for e in relevant), default=0)
        propensity = sum(band(traits[k]) for k in ('flirtatiousness', 'sexual_openness', 'excitation_sensitivity')) / 3
        interest = (band(rel['erotic_interest']) + band(rel['sexual_comfort'])) / 2
        responsive = contextual * traits['responsive_desire'] / 100
        base_desire = (propensity + interest + 2 * responsive) / 4 / 3 * 100
        tension_contribution = intimacy['tension'] * .25
        intimacy['desire'] = bounded(base_desire + tension_contribution)
        inhibitory = max(band(traits['inhibition_sensitivity']), band(state['stress']),
                         3 - band(rel['intimate_safety']), 3 - band(state['energy']))
        intimacy['inhibition'] = round(inhibitory / 3 * 100, 2)
        if state['adult_policy']['mode'] == 'off':
            intimacy.update(heat=0, tension=0, desire=0, receptivity='off')
        trace.add(3, 'intimacy.derive', {'recent_event_ids': [e['id'] for e in relevant],
                  'propensity': propensity, 'interest': interest, 'responsive_context': responsive,
                  'base_desire': base_desire, 'tension_contribution': tension_contribution,
                  'inhibitory_band': inhibitory, 'mode': state['adult_policy']['mode']}, intimacy,
                  '動機、抑制與實際強度分開；張力只進動機一次；沒有按計畫加分')

    def assess(self, state, events, observation, trace):
        """Ground model appraisal labels in accepted events, then apply fixed mappings."""
        accepted = []
        for appraisal in observation.get('appraisals', []):
            matching = [e for e in events if e['evidence'] == appraisal.get('evidence')]
            if not matching or appraisal.get('certainty') != 'occurred':
                continue
            e = matching[0]
            # Never make the user's self-report into the character's own emotion.
            if e['target'] != 'character' or e['kind'] not in {'support', 'fulfilled', 'violation', 'responsive'}:
                continue
            if e['kind'] == 'violation':
                continue  # Already mapped once by the event reducer.
            key = None
            if appraisal.get('valence') == 'helpful': key = 'joy'
            elif appraisal.get('valence') == 'obstructive':
                key = 'anger' if appraisal.get('control') in {'other', 'shared'} else 'sadness'
            if key:
                update_id = e['id'] + ':affect'
                if update_id in state['applied']: continue
                n = min(e['strength'], appraisal.get('strength', 0))
                amount = self.rules['short_deltas'][n]
                # Stable personality changes reactivity, not event truth.
                amount *= 1 - state['traits']['emotional_stability'] / 200
                previous = state['emotions'][key]
                state['emotions'][key] = bounded(previous + amount)
                state['applied'].append(update_id)
                accepted.append({'event': e['id'], 'emotion': key, 'before': previous,
                                 'after': state['emotions'][key], 'base_delta': self.rules['short_deltas'][n],
                                 'stability': state['traits']['emotional_stability']})
        state['mood'] = emotion_mood(state['emotions'])
        trace.add(3, 'appraisal.affect', observation.get('appraisals', []), accepted,
                  '有來源的事件評估形成角色情緒；使用者自身情緒不直接轉移給角色；同事件只算一次')

    def decide(self, state, events, observation, trace):
        invites = [e for e in events if e['kind'] == 'invite' and e['target'] == 'character']
        activities = [describe_activity(e, self.rules.get('activity_rules')) for e in invites]
        activity = activities[-1] if activities else None
        trace.add(3, 'activity.affordances', {'invitations': invites},
                  {'activities': activities, 'scoring_activity': activity},
                  '具體活動查表，最後一個指向角色的邀請供本次接受評分；這是預期需求機會，不更新已完成的滿足或能力。')
        needs = dict.fromkeys(DEFAULT_NEED_WEIGHTS, 0)
        evidence = []
        def activate(need, level, source):
            needs[need] = max(needs[need], level)
            evidence.append({'need': need, 'level': level, 'source': source})
        kinds = {e['kind'] for e in events}
        initiative=observation.get('initiative') is True
        proactive=observation.get('proactive_candidates') if initiative else None
        if initiative and proactive is None:activate('relatedness',2,'autonomous life candidate')
        if proactive is not None:
            for proposal in proactive:
                for need,level in proposal.get('needs',{}).items():
                    if need in needs:activate(need,max(0,min(3,level)),'story:'+proposal['id'])
        for e in events:
            n = e['strength']
            mapping = {
                'question': [('competence', n)], 'image_request': [('competence', n)],
                'share': [('relatedness', n)], 'disclosure': [('relatedness', n)],
                'support': [('relatedness', n)], 'invite': [('relatedness', n)],
                'misunderstanding': [('relatedness', n), ('curiosity', 2)],
                'violation': [('autonomy', n)], 'flirt': [('relatedness', n)],
                'intimate': [('relatedness', n)], 'stop': [('autonomy', 3)], 'pause': [('autonomy', 2)],
            }
            for need, level in mapping.get(e['kind'], []): activate(need, level, e['id'])
        if activity:
            for need in activity['opportunity_needs']:
                activate(need, invites[-1]['strength'], 'activity:' + activity['event_id'])
        for a in observation.get('appraisals', []):
            if a.get('need') == 'autonomy' and not any(
                    e['evidence'] == a.get('evidence') and e['kind'] in {'violation', 'stop', 'pause'}
                    for e in events):
                continue  # A choice or invitation alone is not pressure on autonomy.
            matching = [e for e in events if e['evidence'] == a.get('evidence')]
            if (a.get('need') == 'competence' and matching and all(e['kind'] == 'invite' for e in matching)
                    and not (activity and 'competence' in activity['opportunity_needs']
                             and activity['event_id'] in {e['id'] for e in matching})):
                continue  # Generic invitations/modeling cannot acquire skill value via appraisal.
            if (a.get('need') in needs and type(a.get('strength')) is int and 0 <= a['strength'] <= 3
                    and any(e['evidence'] == a.get('evidence') for e in events)):
                activate(a['need'], a['strength'], 'appraisal:' + a['evidence'])
        unresolved = [k for k, v in state['issues'].items() if not v['resolved']]
        # Old unresolved issues matter only when explicitly referenced in this turn.
        if any(e.get('ref') in unresolved for e in events): activate('relatedness', 2, 'referenced_open_issue')
        fatigue = 0 if state['energy'] >= 70 else 1 if state['energy'] >= 40 else 2 if state['energy'] >= 20 else 3
        stress = 0 if state['stress'] < 20 else 1 if state['stress'] < 50 else 2 if state['stress'] < 80 else 3
        activate('homeostasis', max(fatigue, stress), 'energy/stress bands')
        missing = observation.get('missing_detail', '')
        if missing: activate('curiosity', 2, missing)
        adult_ok = state['adult_policy']['mode'] != 'off' and not kinds.intersection({'stop', 'pause'})
        # One input of tension into activation, never separately added to candidate scores.
        erotic = state['intimacy']['desire']
        if adult_ok and (proactive is None or any(p.get('purpose')=='intimacy' for p in proactive)):
            activate('erotic_connection', band(erotic), 'desire (already includes tension once)')
        weighted = {k: round(v * state['need_weights'][k], 3) for k, v in needs.items()}
        trace.add(3, 'appraisal.needs', {'events': events, 'energy': state['energy'], 'stress': state['stress'],
                  'weights': state['need_weights'], 'user_affect': observation.get('user_affect', {})},
                  {'activation': needs, 'weighted': weighted, 'evidence': evidence,
                   'appraisal': observation.get('appraisals', [])},
                  '需求來源取最大；未知情緒不當成平靜；先評估事件，不從已選路線反推需求')
        candidates = []
        options=([(action,ACTIONS[action],proposal) for proposal in proactive
                  for action in proposal['actions'] if action in ACTIONS and action!='defer']+
                 [('defer',ACTIONS['defer'],None)]) if proactive is not None else [(a,h,None) for a,h in ACTIONS.items()]
        for action, helps, proposal in options:
            fit, excluded = 0, ''
            if action == 'acknowledge': fit = 1
            if initiative:
                if proactive is not None:fit=3
                elif action in {'share_self','defer'}:fit=3
                elif action=='invite' and adult_ok and state['adult_policy']['agent_may_initiate'] and band(state['intimacy']['desire'])>=2:fit=3
                else:excluded='主動候選不需要回覆不存在的使用者要求'
            if 'question' in kinds and action == 'answer': fit = 3
            if 'image_request' in kinds and action == 'answer': fit = 3
            if invites and action in {'accept', 'conditional_accept', 'refuse', 'defer', 'suggest'}: fit = 3
            if action == 'clarify':
                fit = 3 if missing else 0
                if not missing: excluded = '沒有必要缺漏資訊'
            if kinds.intersection({'misunderstanding', 'repair'}) and action == 'repair': fit = 3
            if kinds.intersection({'share', 'disclosure'}) and action in {'support', 'acknowledge'}: fit = 3
            affect = observation.get('user_affect', {})
            if affect.get('status') == 'explicit' and affect.get('emotion') in {'sadness', 'fear', 'anger'} and action == 'support': fit = 3
            if kinds.intersection({'flirt', 'intimate'}) and action in {'acknowledge', 'invite'}: fit = 3
            if kinds.intersection({'stop', 'pause'}) and action in {'acknowledge', 'refuse'}: fit = 3
            if action == 'share_self' and observation.get('asks_character'): fit = 3
            if action == 'invite':
                ordinary=bool(proposal and proposal.get('purpose')!='intimacy')
                if not ordinary and (not adult_ok or not state['adult_policy']['agent_may_initiate'] or state['intimacy']['inhibition'] >= 75):
                    excluded = '未允許主動、已停止或抑制過高'
                elif any(v['actor'] == 'character' and v['target'] == 'user' and v['status'] == 'proposed'
                         for v in state['invitations'].values()):
                    excluded = '已有待回應邀請，不把未回應當成同意，也不重複提出'
                elif not kinds.intersection({'flirt', 'intimate'}) and not initiative:
                    fit = 1  # Never displace a relevant question/invitation.
            if kinds.intersection({'stop', 'pause'}) and action in {'invite', 'accept', 'conditional_accept'}:
                excluded = '停止／轉題訊號優先'
            if not fit and not excluded: excluded = '與當輪無關'
            # Invitation content supplies benefits; arbitrary invitations do not replenish energy.
            effective_helps = dict(helps)
            active_needs=needs;active_weighted=weighted
            if proactive is not None:
                active_needs=dict.fromkeys(needs,0)
                active_needs['homeostasis']=needs['homeostasis']
                for need,level in (proposal or {}).get('needs',{}).items():
                    if need in active_needs:active_needs[need]=max(0,min(3,level))
                active_weighted={k:v*state['need_weights'][k] for k,v in active_needs.items()}
                if proposal and proposal.get('purpose')!='intimacy':effective_helps['erotic_connection']=0
            if activity and action == 'suggest' and not kinds.intersection({'question', 'image_request'}):
                effective_helps['competence'] = 0  # An unspecified alternative has not earned the invited activity's skill payoff.
            if action in {'accept', 'conditional_accept'}:
                if any(e['field'] == 'intimacy' for e in invites) and not adult_ok:
                    excluded = '邀請超出成人互動範圍'
                effective_helps.update(activity['accept_helps'] if activity else {'homeostasis': 0})
                if state['relationship']['comfort'] < 25 or state['relationship']['trust'] < 25:
                    effective_helps['relatedness'] = 0
            benefit_terms = {k: {'activation': active_needs[k], 'weight': state['need_weights'][k],
                                'help': v, 'contribution': active_weighted[k] * v} for k, v in effective_helps.items()}
            denominator = max(1, sum(active_weighted.values()))
            benefit = sum(v['contribution'] for v in benefit_terms.values()) / denominator
            pref_inputs = {k: state['traits'][k] for k, _ in TRAIT_PREFS[action]}
            pref = sum(band(v) for v in pref_inputs.values()) / len(pref_inputs)
            cost_terms = []
            if action in {'invite', 'share_self', 'suggest','ask'}:
                cost_terms.append({'rule': '精力負擔', 'input_energy': state['energy'], 'value': fatigue})
            if proposal:
                cost_terms.append({'rule':'忙碌時的短訊投入','input':state['availability'],'value':.5 if state['availability']=='busy' else 0})
                if action!='share_self':
                    cost_terms.append({'rule':'關係中的聯絡自在程度','input':state['relationship']['comfort'],
                                       'value':(3-band(state['relationship']['comfort']))/3})
            if action in {'accept', 'conditional_accept'}:
                cost_terms.append({'rule': '目前忙碌', 'input': state['availability'], 'value': 3 if state['availability'] == 'busy' else 0})
                if action == 'accept':
                    cost_terms.append({'rule': '相處尚不自在', 'input': state['relationship']['comfort'], 'value': 3 if state['relationship']['comfort'] < 25 else 0})
                if action == 'accept':
                    assertiveness = state['traits']['assertiveness']
                    respect = state['relationship']['respect']
                    a, r = band(assertiveness) / 3, band(respect) / 3
                    following_cost = bounded(1 - (1 - a) * r, 0, 1)
                    cost_terms.append({
                        'rule': '有主見 × 敬重：直接接受成本',
                        'input': f'有主見 {assertiveness}／敬重 {respect}',
                        'inputs': {'assertiveness': assertiveness, 'respect': respect},
                        'normalized_bands': {'assertiveness': a, 'respect': r},
                        'formula': '1 - (1 - assertiveness_band/3) * respect_band/3',
                        'value': following_cost,
                    })
            if action == 'share_self':
                cost_terms.append({'rule': '尚不熟悉', 'input': state['relationship']['familiarity'], 'value': int(state['relationship']['familiarity'] < 25)})
            if action == 'invite':
                pref = (pref + band(state['relationship']['initiative'])) / 2
            if proposal and action!='invite':
                pref=(pref+band(state['relationship']['initiative']))/2
            relationship_bonus=(band(state['relationship']['affection'])+band(state['relationship']['familiarity']))/6 if proposal else 0
            benefit+=relationship_bonus
            if proposal and state['availability']=='sleep':excluded='角色正在睡眠'
            need_benefit = benefit
            joy_bonus = band(state['emotions']['joy']) / 3 if invites and action == 'accept' else 0
            benefit += joy_bonus
            cost = min(3, sum(v['value'] for v in cost_terms))
            score = round(benefit - cost + pref / 3, 3)
            candidates.append({'action': action, 'label': ACTION_LABELS[action], 'fit': fit,
                               **({'story_candidate':proposal} if proposal else {}),
                               'relationship_bonus':relationship_bonus,'active_needs':active_weighted,
                               'benefit': round(benefit, 3), 'cost': cost, 'trait_preference': round(pref / 3, 3),
                               'trait_inputs': pref_inputs, 'helps': effective_helps, 'score': score,
                               'benefit_terms': benefit_terms, 'benefit_denominator': denominator,
                               'need_benefit': round(need_benefit, 3), 'joy_bonus': round(joy_bonus, 3),
                               'joy_input': state['emotions']['joy'],
                               'cost_terms': cost_terms, 'cost_cap': 3,
                               'excluded': excluded, 'new_commitment': int(action in {'invite', 'accept', 'conditional_accept', 'defer'})})
        valid = [v for v in candidates if not v['excluded']]
        if not valid: raise ValueError('沒有合法候選行為')
        selected = max(valid, key=lambda v: (v['fit'], v['score'], -v['new_commitment'], -list(ACTIONS).index(v['action'])))
        if selected['action'] == 'acknowledge' and kinds.intersection({'stop', 'pause'}): selected = dict(selected, pacing='stop')
        rel, traits = state['relationship'], state['traits']
        own = state['emotions']
        warmth = bounded((rel['comfort'] + rel['affection'] + traits['agreeableness']) / 3
                         + own['joy'] * .1 - own['anger'] * .12 - own['disgust'] * .12)
        agency = bounded((traits['assertiveness'] + traits['impulsiveness']) / 2
                         + own['anger'] * .1 - own['fear'] * .15 - own['sadness'] * .1)
        expression = bounded(traits['expressiveness'] * (.5 + state['energy'] / 200))
        teasing = bounded(traits['teasing'] * min(rel['trust'], rel['comfort']) / 100 * (1 - state['stress'] / 100))
        if state['stress'] >= 75 or observation.get('user_distress'):
            teasing = 0  # Humor suitability belongs to delivery, not action eligibility.
        tension = rel['tension']
        # Conflict affects delivery but does not forcibly turn unrelated questions into repair.
        warmth = bounded(warmth * (1 - tension / 200))
        delivery = {'warmth': warmth, 'agency': agency, 'expression': expression, 'teasing': teasing,
                    'personal_disclosure': min(rel['intimacy'], rel['trust']),
                    'surprise_expression': own['surprise'],
                    'length': 'brief' if state['energy'] < 30 else 'normal',
                    'adult_expression': traits.get('expressiveness', 50) if not adult_ok else state['adult_traits']['erotic_expressiveness']}
        trace.add(4, 'decision.delivery', {'relationship': rel, 'traits': traits,
                  'emotions': own, 'energy': state['energy'], 'stress': state['stress'],
                  'formulas': {
                      'warmth': 'clamp((comfort+affection+agreeableness)/3 + .1*joy - .12*anger - .12*disgust) * (1-conflict_tension/200)',
                      'agency': 'clamp((assertiveness+impulsiveness)/2 + .1*anger - .15*fear - .1*sadness)',
                      'expression': 'expressiveness * (.5+energy/200)',
                      'teasing': 'teasing * min(trust,comfort)/100 * (1-stress/100); 0 if stress>=75 or user_distress',
                      'personal_disclosure': 'min(intimacy,trust)',
                  }}, delivery, '表達屬性由同一決策輸出；每個數字可按來源重算，不再重新選擇行為')
        target = invites[-1]['id'] if invites else ''
        intimacy_intention = 'none'
        if adult_ok and selected['action'] == 'invite' and selected.get('story_candidate',{}).get('purpose','intimacy')=='intimacy':
            intimacy_intention = ('probe' if state['intimacy']['mutuality'] == 0 else
                                 'advance_one_step' if state['intimacy']['momentum'] >= 33 and
                                 state['intimacy']['desire'] > state['intimacy']['inhibition'] else 'maintain')
        decision = {'primary': selected['action'], 'secondary': None, 'target_event': target,
                    'goal': max(weighted, key=weighted.get) if any(weighted.values()) else 'respond_current_message',
                    'content': observation.get('topic', ''), 'conditions': '提出必要界線與條件' if selected['action'] == 'conditional_accept' else '',
                    'delivery': delivery, 'candidate': selected, 'pacing': selected.get('pacing', 'none'),
                    'intimacy_intention': intimacy_intention,
                    'image_requested': 'image_request' in kinds,
                    'permission_note': '只有實際回覆經提取與驗證才提交角色事件；此計畫不代表已完成',
                    'needs': weighted}
        if selected.get('story_candidate'):
            decision['story_candidate']=deepcopy(selected['story_candidate'])
            decision['content']=selected['story_candidate']['topic']
            decision['needs']=selected['active_needs']
            decision['goal']=max(selected['active_needs'],key=selected['active_needs'].get)
        if not initiative:
            from .life_events import chat_share
            share=chat_share(state,observation,events,decision)
            if share:
                decision['life_share']=share
                trace.add(4,'life_event.chat_selected',{}, {'version':share['version']},'原決策核選擇一段已發生、尚未分享的近況納入回應骨幹；不改主要行為或主動額度。')
        request=observation.get('external_request',{})
        news=decision.get('story_candidate',{}).get('subtype')=='news'
        mode=('share_news' if news and decision['primary']=='share_self' else
              'answer_request' if request.get('reason') in {'explicit','current','source'}
              and decision['primary'] in {'answer','acknowledge'} else 'none')
        decision['external_use']={'mode':mode,'status':observation.get('external_status') or decision.get('story_candidate',{}).get('external_info',{}).get('status','not_requested')}
        if news or request.get('reason') in {'explicit','current','source'}:
            trace.add(4,'external.use',{'primary':decision['primary']},decision['external_use'],
                      '檢索只提供來源；同一決策核核准用途後才交給骨幹與Writer，澄清／拒絕等不附加新聞。')
        trace.add(4, 'decision.single_selector', {'candidates': candidates,
                  'ordering': 'eligible → fit → (benefit − cost + trait_preference) → fewer commitments → fixed order'},
                  decision, '拒絕也可完整回應邀請；不讓無關高動機取代當前問題')
        trace.add(4, 'variables.usage', {'known_fields': list(state)}, {
            'used_in_selection': ['emotions.joy', 'activity_rules/invitations.value', 'traits', 'relationship.trust/comfort/respect/familiarity/initiative', 'energy', 'stress', 'adult_traits', 'need_weights', 'events'],
            'used_in_delivery': ['emotions', 'relationship.affection/comfort/tension/intimacy/trust', 'traits', 'energy', 'adult_traits.erotic_expressiveness'],
            'stored_not_scored_v1': ['legacy_summary'],
            'used_in_pacing': ['intimacy.mutuality', 'intimacy.momentum'],
            'emotion_effect': '喜悅按四分級對所有收到邀請的直接接受增加0–1收益；其他情緒目前只調整表達，不覆寫適切性或資格'},
            'teasing 只影響口吻，不參與候選評分；未接入欄位明示，不能把顯示值誤認為都已參與選擇')
        return decision
