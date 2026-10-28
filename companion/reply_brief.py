"""Compile the chosen action into a small expression contract; no scoring or new state."""
ACTION_OUTLINES = {
    'answer':'直接回答本輪問題。',
    'clarify':'只詢問繼續所需的一項缺漏。',
    'ask':'詢問一個與已知話題相關、對方可自由選擇回答的問題。',
    'support':'回應對方明確感受並給予支持。',
    'share_self':'分享有來源的自身近況。',
    'accept':'明確答應本次邀請；活動尚未開始或完成。',
    'conditional_accept':'答應本次邀請並說明必要條件；活動尚未開始或完成。',
    'refuse':'有禮地拒絕本次活動，拒絕範圍限於該要求。',
    'defer':'暫緩本次要求，說明有來源的當下限制。',
    'suggest':'提出可選的替代方案；尚未執行。',
    'repair':'處理有來源的誤解或衝突。',
    'invite':'提出一次可自然拒絕的邀請；尚未接受或完成。',
    'acknowledge':'回應當前內容。',
}
ENGAGEMENT_OUTLINES = {
    'continue':'維持有禮的交談空間，無須固定追問或結尾。',
    'brief':'簡短回應，仍願意交談；忙碌只限制投入程度。',
    'respect_pause':'尊重停止或暫緩，留給對方決定是否續聊。',
    'rest':'簡短說明需要休息，讓對方可稍後聯絡，不承諾時間。',
}


def compile_reply_brief(state, decision, events=()):
    signals={e['kind'] for e in events}
    if signals.intersection({'stop','pause','violation'}) or decision.get('pacing')=='stop':engagement='respect_pause'
    elif state['availability']=='sleep':engagement='rest'
    elif state['availability']=='busy' or state['energy']<30:engagement='brief'
    else:engagement='continue'
    primary=decision['primary']
    stance={'accept':'accepted','conditional_accept':'conditional','refuse':'declined',
            'defer':'deferred','suggest':'alternative'}.get(primary,'not_applicable')
    life=decision.get('life_share')
    detail=''
    if life:
        lead='以這件已發生的自身近況回應，最多一至兩句，不假設使用者參與：' if life.get('mode')=='answer' else '先完整回應本輪內容；若自然接得上，再用一句話提及這件已發生的自身近況，不假設使用者參與：'
        detail=lead+life['text']
    external=decision.get('external_use',{}).get('mode')
    if external in {'answer_request','share_news'}:
        detail+='按本輪檢索狀態'+('回應使用者的公開資訊問題' if external=='answer_request' else '分享已選定的一則新聞')+'；只使用提供的來源摘要，不轉成角色親歷或魔法世界事件。未取得資料則明說限制，不編造結果。'
    return {'activity_stance':stance,'chat_engagement':engagement,
            'length':'brief' if engagement!='continue' else decision['delivery'].get('length','normal'),
            'outline':(decision.get('story_candidate',{}).get('outline') or ACTION_OUTLINES[primary])+ENGAGEMENT_OUTLINES[engagement]+detail}


def attach_memory_answer(contract, packet):
    """Same selected action, with an explicit source/unknown handoff for recall."""
    contract['decision']['memory_answer'] = packet
    contract['decision']['response_outline'] += '回憶部分依memory_answer區分正式紀錄、曾有的說法與缺失；不補完未知，不另加追問。'
    return contract
