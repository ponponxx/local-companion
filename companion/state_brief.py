"""Transport-only state header; never stored as character speech or model input."""
from .visual_state import current_appearance, SLOT_NAMES


def state_brief(state):
    scene = state.get('scene', {})
    location = scene.get('character_location') or scene.get('location') or '未確認'
    user_location = scene.get('user_location') or '未確認'
    lines = ['【目前狀態｜系統紀錄】', f'地點：角色在{location}；你的位置：{user_location}']
    from .journeys import pending_scene_handoff
    handoff=pending_scene_handoff(state)
    if handoff:
        from .pyramid import date
        at=date(handoff['at']).strftime('%m月%d日 %H:%M')
        notice='當時未通知' if handoff.get('notice_status')=='silent' else '先前通知已送達' if handoff.get('notice_status')=='delivered' else '未確認通知送達'
        pause=f"對話暫停{handoff['idle_minutes']}分鐘後，" if handoff.get('idle_minutes',0)>=10 else ''
        lines.append(f"換場經過：{at}，{pause}角色依行程從{handoff['origin']}移到{handoff['destination']}（{notice}）。")
    pending = scene.get('pending') or {}
    if pending.get('location'):
        phase = '移動中，尚未抵達' if pending.get('delivery') in {'delivered', 'simulated_delivered'} else '預計前往，尚未通知完成'
        lines.append(f'{phase}：{pending["location"]}')
    for person,trip in scene.get('journeys',{}).items():
        if trip.get('status')=='moving':
            lines.append(('你' if person=='user' else '角色')+'正在前往：'+trip['destination']+'（尚未抵達）')
    appearance = current_appearance(state.get('appearance', {}))
    lines.append('髮型：' + (appearance.get('hair') or '未確認'))
    known, unknown = [], []
    for slot, label in SLOT_NAMES.items():
        value = appearance['clothing'][slot]
        if value == '未確認':
            unknown.append(label)
        else:
            known.append(f'{label}：{value}')
    lines.append('衣著：' + ('；'.join(known) if known else '尚未確認'))
    if unknown:
        lines.append('未確認：' + '、'.join(unknown))
    return '\n'.join(lines)


def with_state_brief(state, reply):
    return state_brief(state) + '\n\n' + reply
