from .flow_debug import human
"""Human-readable rendering of actual trace nodes. No model or policy decisions."""
import asyncio
import logging
import time
from .pyramid import DecisionTrace, ACTION_LABELS
from .store import TRAIT_LABELS
from .visual_state import current_appearance, clothing_baseline, SLOT_NAMES

log = logging.getLogger(__name__)
LABELS = dict(ACTION_LABELS, relatedness='連結', autonomy='自主', competence='勝任',
              curiosity='好奇', homeostasis='身心恢復', erotic_connection='親密連結',
              joy='喜悅', sadness='悲傷', anger='憤怒', fear='恐懼', disgust='厭惡', surprise='驚訝',
              energy='體力', stress='壓力', trust='信任', comfort='舒適', respect='敬重',
              familiarity='熟悉', affection='好感', tension='張力', desire='靠近動機',
              heat='親密熱度', inhibition='抑制', mutuality='互相承接', momentum='互動動量',
              warmth='溫暖', agency='主見', expression='表達強度', teasing='玩笑口吻',
              personal_disclosure='自我揭露', user='使用者', character='角色',
              question='提問', invite='邀請', share='分享', promise='承諾', stop='停止', pause='暫停',
              flirt='曖昧', intimate='親密互動', image_request='要求圖片', unknown='無法確定',
              move='移動', appearance='外觀變化', care='照顧', task='任務', social='社交', intimacy='親密', other='其他')


LABELS.update(TRAIT_LABELS)
LABELS.update({'balanced':'適中','brief':'簡短','normal':'一般','length':'長度','adult_expression':'親密表達強度','scene':'場景','appearance':'外觀','user_profile':'使用者認知','invitations':'邀請','activities':'活動'})

def label(key): return LABELS.get(key, key)
def num(value): return f'{value:.3f}'.rstrip('0').rstrip('.') if isinstance(value,(float,int)) else str(value)
def pairs(values): return '、'.join(f'{label(k)} {num(v)}' for k,v in values.items()) or '無'


def describe(node):
    """Only render known diagnostic fields, never raw prompts or model prose."""
    if node['rule'].startswith('external.'):
        o=node.get('output',{})
        if node['rule']=='external.use':return '外部資料用途定案',{'answer_request':'用於回答公開資訊問題','share_news':'分享選定新聞','none':'本輪不採用來源'}.get(o.get('mode'),'未指定')+'。'+node.get('reason','')
        return ('外部資料',f"狀態：{o.get('status')}；採用來源 {o.get('sources',0)} 筆；"+node.get('reason',''))
    rule=node['rule'];i=node.get('inputs') or {};o=node.get('output') or {}
    if rule=='delivery.lookup':
        lines=['四級查表：0–24／25–49／50–74／75–100；不重選行為。']
        for key,level in i.get('levels',{}).items():
            lines.append(f'{label(key)}：第{level}級 → {o.get(key, "")}')
        guards={'pause_or_rest':'停止或休息','repair_or_support':'修復或支持','high_stress':'高壓力'}
        if i.get('guards'):lines.append('優先規則：'+'、'.join(guards.get(g,g) for g in i['guards']))
        lines.append(o.get('verbal_habits',''))
        return '口吻查表', '\n'.join(lines)
    if rule=='scene.initialize':return '建立首次現址',node.get('reason','')+'；地點：'+o.get('character_location','未確認')
    if rule=='life.plan_failed':return '日程尚未建立',o.get('error','未知原因')+'；既有現況保留，不補造行程。'
    if rule=='context.sections':
        return 'Writer各區容量',f"總輸入 {o.get('total')} tokens；近期原文約 {o.get('recent_messages')}、互動筆記約 {o.get('current_interaction')}、故事橋接約 {o.get('story_context')}。各區是移除後的token差，不直接相加。"
    if rule=='working.updated':
        actions={'add':'新增','confirm':'雙方承接','replace':'更正／衝突','close':'結束'}
        statuses={'reported':'單方說法','shared':'雙方已承接','corrected':'已更正','closed':'已結束'}
        lines=[]
        for v in o.get('accepted',[]):
            if v.get('conflict_recorded'):
                lines.append('衝突已保留，現況未覆寫：'+v['text']+'\n維持：'+v.get('current_text',''))
                continue
            action=actions.get(v['op'],v['op'])
            if v['status']=='reported' and v['op'] in {'replace','close'}:action='待另一方確認的'+('更正' if v['op']=='replace' else '結束')
            lines.append(action+'：'+v['text']+'（'+statuses.get(v['status'],v['status'])+'）')
        if o.get('rejected'):lines.append('未採用：'+'、'.join(o['rejected']))
        return '當前互動筆記更新','\n'.join(lines) or '沒有新增；保留既有有效設定。'
    if rule=='memory.packet':return 'Writer統一記憶閱讀版',o.get('text') or '本輪沒有補充記憶。'
    if rule=='memory.pending_nonblocking':return '待整理工作不阻塞聊天',human(o)
    if rule=='memory.response_rebase':return '延後分析提交檢查',human(o)
    if rule=='working.reading':return 'Writer實際互動筆記',o.get('text') or '目前沒有可用筆記。'
    if rule=='appearance.observation':
        return '外觀判讀與來源檢查',f"通過來源檢查的候選更新 {o.get('accepted',0)} 項；拒收："+'；'.join(o.get('rejected',[]))
    if rule.startswith('life_event.'):
        if rule=='life_event.status':return '生活事件等待狀態',f"本場景規劃 {o.get('planned',0)}；待發生 {o.get('drafts',0)}；已發生 {o.get('occurred',0)}；未分享 {o.get('unshared',0)}。"+('等待：'+o['blocked'] if o.get('blocked') else '條件已就緒。')
        if rule=='life_event.planned':return '新場景事件候選',f"本次規劃 {o.get('count',0)} 項，尚未發生。"
        if rule=='life_event.chat_selected':return '聊天近況已選入骨幹','使用已發生、未分享事件，不增加主動訊息計數。'
        titles={'committed':'生活事件已發生','expired':'故事保留但不再推進','deferred':'故事續寫延後',
                'stale':'取消過時故事草稿','invalid_drafts':'拒收生活事件草案','contribution':'收到對故事的建議',
                'input_rejected':'拒收無來源故事修改','disclosed':'已分享生活事件'}
        lines=[o['title']] if o.get('title') else []
        if 'emotion' in o:lines.append(f'{label(o["emotion"])}：{num(o["before"])} → {num(o["after"])}')
        lines.append(node.get('reason',''))
        return titles.get(rule.split('.')[-1],'生活事件'), '\n'.join(lines)
    if rule=='pipeline.queue':return '訊息排隊時間', '未記錄' if o.get('seconds') is None else f'{num(o["seconds"])} 秒。'
    if rule=='response.scheduled':return '回文分析已排程','文字已送達。背景驗證後更新狀態與故事；指令可中斷，下一句聊天不等待舊分析，沿用最後確認狀態。'
    if rule=='response.stale':return '捨棄過時分析','狀態已變動或收到新訊息；未提交分析結果，保留待處理來源。'
    if rule=='pipeline.context':
        return '上下文組裝耗時', f'{num(node.get("seconds",0))} 秒；實際tokenizer呼叫 {o.get("tokenizer_calls",0)} 次，相同輸入快取 {o.get("cache_hits",0)} 次。'
    if rule=='pipeline.reply_delivered':return '文字送達耗時',f'開始處理到確認送達 {num(node.get("seconds",0))} 秒；不含先前佇列等待。'
    if rule=='pipeline.response_wait':return '回文狀態同步耗時',f'{"背景" if o.get("background") else "前景必要依賴"}：{num(node.get("seconds",0))} 秒。'
    if rule=='turn.snapshot':
        return '本輪起點', '已凍結狀態；判讀不等於確認事實。\n身心：'+pairs({k:o[k] for k in ('energy','stress') if k in o})+'\n情緒：'+pairs(o.get('emotions',{}))+'\n關係：'+pairs(o.get('relationship',{}))+'\n當前衣著：'+'、'.join(f'{SLOT_NAMES[k]}：{v}' for k,v in current_appearance(o.get('appearance',{}))['clothing'].items())+f'\n無法確定的基準描述：{len(clothing_baseline(o.get("appearance",{}).get("legacy_outfit",""),allow_layers=True)[1])} 段（沒有當成穿著事實）。'
    if rule=='time.advance':
        before=i.get('before',{});lines=[]
        for k in ('energy','stress'):
            if k in o:lines.append(f'{label(k)}：{num(before.get(k))} → {num(o[k])}')
        for k,v in o.get('emotions',{}).items():
            if v!=before.get('emotions',{}).get(k):lines.append(f'{label(k)}：{num(before.get("emotions",{}).get(k))} → {num(v)}')
        return '時間推移', '\n'.join(lines) or '沒有可列出的數值變化。'
    if rule=='story.prepared':
        return '故事脈絡取材', f'故事預算 {o.get("token_budget")} tokens；實際增加 {o.get("tokens") if o.get("tokens") is not None else "未計數"} tokens；相關記憶 {o.get("memory_items",0)} 筆。未放入的近期往返 {o.get("uncovered_exchanges",0)} 組；原文沒有刪除。'
    if rule=='proactive.pool':
        lines=['可延續話題：'+('、'.join(c['topic'] for c in o.get('candidates',[])) or '無')]
        if o.get('blocked'):lines.append('本次等待原因：'+o['blocked'])
        lines.extend(c['topic']+'：'+c['reason'] for c in o.get('excluded',[]))
        return '故事主動聯絡候選', '\n'.join(lines)
    if rule=='proactive.invalidated':return '取消過時主動訊息','來源或設定已改變，未傳送。'
    if rule=='story.events':
        stages={'completed':'描述已完成','ongoing':'描述進行中','planned':'計畫','proposed':'提議',
                'hypothetical':'假設','negated':'否定發生','unknown':'未知'}
        lines=[f'發話者 {label(e["speaker"])}；行動者 {label(e["actor"])}；對象 {label(e["target"])}；{stages[e["stage"]]}：{e["action"]}' for e in o.get('events',[])]
        fallback='模型未提供故事動作欄位，保留原話。' if not o.get('available') else '沒有通過來源檢查的具體動作，保留原話。'
        return '故事人物與動作', '\n'.join(lines or [fallback])+f'\n拒收 {o.get("rejected",0)} 項。這些是有來源的說法，不直接改正式狀態。'
    if rule=='story.reading':
        return 'Writer 實際故事脈絡', o.get('text') or '本輪沒有額外故事脈絡；使用選入的近期原文。'
    if rule=='story.background_deferred':
        return '背景故事整理延後', node.get('reason','本批未提交，原文保留。')
    if rule=='context.prepared':
        stage={'user_observer':'使用者觀察器','response_observer':'回應後觀察器','writer':'角色Writer','proactive_writer':'主動訊息Writer'}.get(i.get('stage'),'模型')
        tokens=str(o.get('input_tokens')) if o.get('tokenizer_verified') else '未提供計數'
        return '完成本次上下文組裝', f'{stage}：輸入 {tokens} tokens；預留輸出 {o["output_tokens"]}，安全 {o["safety_tokens"]}，總容量 {o["capacity"]}。\n保留近期 {o["recent_messages"]} 則／{o["recent_groups"]} 組；連續摘要 {len(o["continuity_sources"])} 個來源；長期記憶 {o["memory_items"]} 筆。\n去除重複來源 {o["duplicates_omitted"]} 筆；餘量不足未取用 {o["memory_omitted"]} 筆。各階段獨立分配，原始資料仍保留。'
    if rule=='context.output_reserve':
        return '借用Observer輸出預留', f'預留輸出 {i.get("previous_output_tokens")} → {o.get("output_tokens")} tokens；必要輸入 {o.get("input_tokens")}，新的輸入上限 {o.get("input_limit")}。'+node.get('reason','')
    if rule=='context.capacity_failed':
        return '必要上下文放不下', f'必要輸入 {o.get("input_tokens")} tokens，可用上限 {o.get("input_limit")}。此容量檢查尚未通過，沒有清空近期對話或刪除原文；是否繼續請看後續預留調整與本輪結束狀態。'
    if rule=='writer.context_audit':
        return '檢查組裝後的Writer輸入', f'已知結構問題 {len(o.get("issues",[]))} 項；重複長文字 {o.get("structured_duplicate_count",0)} 項。這是格式與已知矛盾檢查，不代表語義完全正確。'
    if rule=='scene.activity_reconciled':
        return '修正抵達後活動', '已有抵達紀錄，移除誤沿用的途中活動；沒有目的地活動來源則保留未知，不猜補。'
    if rule=='memory.recall':
        return '取回記憶候選', f'候選 {len(o.get("items",[]))} 筆；檢索方式：'+{'hybrid':'關鍵字＋語意','lexical':'關鍵字','lexical_fallback':'語意不可用，退回關鍵字'}.get(o.get('mode'),str(o.get('mode','未知')))+'。原文不在此重複貼出。'
    if rule=='memory.coverage':
        return '整理舊對話', f'整理來源索引 {i.get("batch_start")}–{i.get("batch_end",0)-1}；新增 {len(o.get("notes",[]))} 筆。原文仍保留。'
    if rule=='memory.proposal_rejected':
        return '記憶整理備援', '模型整理未通過格式或來源驗證；整批改存有來源原文，繼續本輪。'
    if rule=='memory.context_budget':
        return '調整記憶容量', f'本輪檢索預算 {i.get("chars")} → {o.get("chars")} 字元；沒有刪除儲存的記憶。'
    if rule=='observation.context_examples':
        return '調整判讀容量', '上下文不足，省略輔助範例後重試。規則與本輪來源資料保留。'
    if rule=='model.failed':
        return '模型呼叫受阻', '上下文容量不足，後續會嘗試降載。' if o.get('reason_code')=='context_capacity' else '模型服務呼叫失敗；詳細診斷保留本機，不當成角色台詞。'
    if rule=='model.request':
        if not i.get('structured'): title='9B Writer 已完成措辭'
        elif node['layer']==1:title='9B 已完成記憶整理'
        else:title='9B 已完成事件判讀'
        m=o.get('metrics',{})
        return title, f'耗時 {num(node.get("seconds",0))} 秒；模型鎖等待 {num(m.get("queue_seconds",0))} 秒；容量預檢 {num(m.get("preflight_seconds",0))} 秒。接著由後端驗證結果。'
    if rule=='observation.contract':
        u=i.get('utterance',{});events=o.get('events',[])
        lines=[f'話語行為：{label(u.get("act","unknown"))}；用途：{label(u.get("purpose","other"))}；影響強度：{u.get("strength",0)}／3。',
               '提案：'+('、'.join(f'{label(e.get("actor",""))} → {label(e.get("kind",""))}（{label(e.get("target",""))}）' for e in events) or '沒有額外事件')]
        return ('第一次判讀：使用者' if node['layer']==2 else '回應後判讀：角色'), '\n'.join(lines)
    if rule=='observation.appraisal':
        a=o.get('user_affect',{});known=a.get('status')=='explicit'
        lines=['使用者情緒：'+(label(a.get('emotion',''))+'（明確表達）' if known else '未知，不當成平靜。'),
               '是否缺少必要資訊：'+('是' if o.get('missing_detail') else '否')]
        for a in o.get('appraisals',[]):
            lines.append(f'9B 評估 {label(a["need"])}：'+{'helpful':'有助滿足','obstructive':'受阻','neutral':'中性','unknown':'未知'}.get(a['valence'],'未知')+'；'+{'occurred':'已發生','possible':'可能發生','unknown':'未知'}.get(a['certainty'],'未知')+f'；強度 {a["strength"]}／3（仍須事件驗證）。')
        return '同一次 9B 的情緒與事件評估', '\n'.join(lines)
    if rule=='events.validate_reduce':
        lines=[f'接受 {len(o.get("accepted",[]))} 項，拒收 {len(o.get("rejected",[]))} 項。']
        for k,delta in o.get('changes',{}).items():
            a,b=delta.get('before'),delta.get('after')
            if isinstance(a,(int,float)) and isinstance(b,(int,float)):lines.append(f'{label(k)}：{num(a)} → {num(b)}')
            elif k in {'emotions','relationship','intimacy'} and isinstance(a,dict) and isinstance(b,dict):
                lines.extend(f'{label(f)}：{num(a.get(f))} → {num(v)}' for f,v in b.items() if isinstance(v,(int,float)) and a.get(f)!=v)
        for rejected in o.get('rejected',[]):
            reason=rejected.get('reason','')
            # Validation reasons are fixed backend strings; unknown exceptions stay private.
            known=('證據不在對應說話者原文','角色回文無權建立使用者行動','提議或假設不等於已發生',
                   '找不到對應邀請','只有受邀者可回應尚待處理的邀請','尚未接受，不能開始','尚未開始，不能完成',
                   '不允許的種類、人物或階段','影響等級必須為 0–3','邀請用途分類無效',
                   '自主模式：使用者不能替角色建立意願或內心狀態')
            lines.append('拒收原因：'+(reason if reason in known else '未通過事件格式、來源權限或轉移驗證。'))
        lines.append('其他有變動的狀態：' +('、'.join(label(k) for k in o.get('changes',{}) if k in {'scene','appearance','user_profile','invitations','activities'}) or '無'))
        return '驗證與狀態更新', '\n'.join(lines)
    if rule=='intimacy.derive':
        return '親密動態推導', pairs({k:o[k] for k in ('heat','tension','desire','inhibition','momentum','mutuality') if k in o})+'。這些是狀態，不代表權限或已完成行動。'
    if rule=='appraisal.affect':
        return '事件造成的角色情緒', '\n'.join(f'{label(v["emotion"])}：{num(v["before"])} → {num(v["after"])}（基礎增量 {num(v["base_delta"])}、情緒穩定 {num(v["stability"])}）' for v in o) if o else '沒有通過來源檢查的情緒更新；保留當下情緒。'
    if rule=='activity.affordances':
        a=o.get('scoring_activity')
        return '活動分類', (f'查表分類：{a["label"]}。{a["reason"]} 可滿足項：{pairs(a.get("accept_helps",{}))}。' if a and a.get('matched') else '本輪活動沒有匹配到具體類型，僅沿用已定義的用途備援；不猜測勝任收益。' if a else '本輪沒有指向角色的活動邀請。')
    if rule=='appraisal.needs':
        lines=[]
        for k,v in o.get('activation',{}).items():
            lines.append(f'{label(k)}：活化 {num(v)} × 權重 {num(i.get("weights",{}).get(k,0))} = {num(o.get("weighted",{}).get(k,0))}')
        event_types={e['id']:e['kind'] for e in i.get('events',[])}
        for source in o.get('evidence',[]):
            raw=source.get('source','')
            reason=(label(event_types[raw])+'事件' if raw in event_types else
                    '有來源的故事或生活候選' if raw.startswith('story:') else
                    '具體活動查表' if raw.startswith('activity:') else
                    '已驗證事件的模型評估' if raw.startswith('appraisal:') else
                    {'energy/stress bands':f'體力 {num(i.get("energy"))}、壓力 {num(i.get("stress"))} 的區間',
                     'desire (already includes tension once)':'靠近動機的區間（已包含一次張力）',
                     'referenced_open_issue':'本輪提到未解決問題','autonomous life candidate':'背景主動聯絡'}.get(raw,'需要補齊的必要資訊'))
            lines.append(f'來源：{reason} → {label(source["need"])} 活化 {num(source["level"])}')
        return '需求活化與加權', '\n'.join(lines)+'\n活化採各來源最大值；不是把同需求重複加總。' 
    if rule=='decision.single_selector':
        lines=['先比較資格與適切分，再比較「收益 − 成本 ＋ 個性偏好」；不是只看總分。']
        for c in i.get('candidates',[]):
            mark='★ 選中' if c['action']==o.get('primary') else '候選'
            lines.append(f'{mark} {c["label"]}：適切 {c["fit"]}；收益 {num(c["benefit"])} − 成本 {num(c["cost"])} ＋ 偏好 {num(c["trait_preference"])} = {num(c["score"])}'+(f'；排除：{c["excluded"]}' if c.get('excluded') else ''))
            benefits=[f'{label(k)}：{num(v["activation"])} × {num(v["weight"])} × 滿足程度 {num(v["help"])} = {num(v["contribution"])}' for k,v in c.get('benefit_terms',{}).items() if v['contribution']]
            lines.append('  收益分子：'+('、'.join(benefits) or '0')+f'；除數 {num(c.get("benefit_denominator",1))}；喜悅額外收益 {num(c.get("joy_bonus",0))}')
            if c.get('story_candidate'):lines.append('  話題：'+c['story_candidate']['topic']+'；關係連結額外收益 '+num(c.get('relationship_bonus',0)))
            lines.append('  成本：'+('、'.join(f'{t["rule"]} {num(t["value"])}' for t in c.get('cost_terms',[])) or '0')+f'（合計上限 {c.get("cost_cap",3)}）')
            lines.append('  偏好輸入：'+pairs(c.get('trait_inputs',{}))+'（先轉四級，再取平均與縮放）')
        lines.append(f'定案：{label(o.get("primary",""))}；主要目標：{label(o.get("goal",""))}。同分時較少新承諾優先，再依固定順序。')
        return '比較所有候選行為', '\n'.join(lines)
    if rule=='decision.delivery':
        return '計算口吻', '、'.join(f'{label(k)} {num(v) if isinstance(v,(int,float)) else label(v)}' for k,v in o.items())+'。這一步只決定說法，不重新選擇答應或拒絕。'
    if rule=='decision.expression_brief':
        stance={'accepted':'接受','conditional':'附條件接受','declined':'拒絕活動','deferred':'暫緩','alternative':'替代方案','not_applicable':'非活動邀請'}.get(o.get('activity_stance'),'未知')
        chat={'continue':'保持交談','brief':'忙碌但可簡短聊','respect_pause':'尊重停止或暫緩','rest':'需要休息'}.get(o.get('chat_engagement'),'未知')
        return '本輪簡短骨幹', f'活動意願：{stance}；交談投入：{chat}。\n{o.get("outline","")} 個性只調說法，不改這份定案。'
    if rule=='writer.consistency':
        labels={'consistent':'一致','match':'一致','mismatch':'偏離定案','unknown':'無法確認'}
        if 'conversation_status' in o:
            return '核對實際回覆', '活動／話語行為：'+labels.get(o.get('activity_status'),'無法確認')+'；交談態度：'+labels.get(o.get('conversation_status'),'無法確認')+'。依同一次回應後判讀及逐字證據核對，不新增Judge或自動改寫。'
        return '核對實際回覆', labels.get(o.get('status'),'無法確認')+'；未自動重寫。'
    if rule=='telegram.response_deferred':return '前輪補分析', '前輪已送達回覆仍無法分析；保留待處理，不重送，繼續本輪。'
    if rule=='telegram.response_recovered':return '前輪補分析', '已完成前輪回覆分析，沒有重新發送角色回覆。'
    if rule=='variables.usage':
        return '哪些參數實際有參與', o.get('emotion_effect','')+'\n玩笑只影響口吻，不是另一個候選行為；不能因此改變答應或拒絕。'
    if rule=='debug.finished':return '本輪結束', {'done':'角色回覆已送達，回應後分析已完成。','failed':'本輪在角色回覆送達前停止。','deferred':'角色回覆已送達，回應後分析延後。','cancelled':'本輪已取消。'}.get(o.get('status'),'本輪已結束。')+({'JSONDecodeError':' 原因：模型輸出不是合法 JSON。','ValueError':' 原因：資料或狀態驗證未通過。','MemoryProposalError':' 原因：記憶來源驗證未通過。','ServiceError':' 原因：模型或傳輸服務未能完成。'}.get(o.get('error_type'),''))
    if rule=='memory.tool_turn_excluded':return '圖片工具回合', '本輪不寫入聊天記憶；已確認的場景與外觀仍可更新。'
    return None


class DebugTrace(DecisionTrace):
    def __init__(self, reporter, clock=None):
        super().__init__();self.reporter=reporter
        self.clock=clock or time.monotonic
        self.started=self.previous=self.clock()
    def add(self,*args,**kwargs):
        now=self.clock()
        kwargs['elapsed_seconds']=round(max(0,now-self.started),3)
        kwargs['interval_seconds']=round(max(0,now-self.previous),3)
        self.previous=now
        super().add(*args,**kwargs)
        self.reporter.feed(self.nodes[-1])


def message_chunks(text, limit=3200):
    """Telegram limits UTF-16 units; keep non-BMP characters intact."""
    chunk=[];used=0
    for char in text:
        width=2 if ord(char)>0xffff else 1
        if used+width>limit:
            yield ''.join(chunk);chunk=[];used=0
        chunk.append(char);used+=width
    if chunk:yield ''.join(chunk)


class DebugReporter:
    def __init__(self, telegram, chat_id, turn_id, mode="detail"):
        self.telegram,self.chat_id,self.turn_id=telegram,chat_id,turn_id
        self.mode=mode
        self.queue=asyncio.Queue();self.step=0;self.disabled=False
        self.task=asyncio.create_task(self.run())

    def feed(self,node):
        if self.disabled:return
        try:
            if self.mode=="flow":
                from .flow_debug import describe_flow
                rendered=describe_flow(node)
            else:rendered=describe(node)
        except Exception:
            log.warning('Debug formatting failed: %s',node.get('rule','unknown'));return
        if rendered:
            self.step+=1
            title,body=rendered
            if 'seconds' in node:
                timing=f'實測耗時 {node["seconds"]:.3f} 秒'
            elif 'interval_seconds' in node:
                timing=f'節點間隔 {node["interval_seconds"]:.3f} 秒（含等待，非獨立運算耗時）'
            else:timing='耗時未記錄'
            if 'elapsed_seconds' in node:timing+=f'｜本輪累計 {node["elapsed_seconds"]:.3f} 秒'
            self.queue.put_nowait(f'步驟 {self.step}｜{title}\n{timing}\n{body}')

    async def run(self):
        while True:
            item=await self.queue.get()
            items=[item]
            try:
                # Group adjacent synchronous steps to reduce Telegram message traffic.
                await asyncio.sleep(.15)
                while not self.queue.empty():items.append(self.queue.get_nowait())
                content='\n\n'.join(items)
                if not self.disabled:
                    for part in message_chunks(content):
                        await asyncio.wait_for(self.telegram.send(self.chat_id,f'【系統 Debug｜{self.turn_id}】\n\n'+part),timeout=8)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.disabled=True
                log.warning('Debug delivery disabled: %s',type(exc).__name__)
            finally:
                for _ in items:self.queue.task_done()

    async def flush(self):await self.queue.join()

    async def close(self):
        try:await self.flush()
        finally:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
