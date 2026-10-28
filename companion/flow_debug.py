"""Display recorded boundary data, not chain-of-thought or decision arithmetic."""
import json

KEYS={'proactive_continuation':'主動話題接續','intent':'本次目的','prior_exchange':'之前的對話','contact_status':'後續發話狀況','already_sent':'上次已送出內容（避免照抄）','previous_contact_unavailable':'原話未提供','instruction':'讀法','identity':'身份','character_definition':'角色設定','current_facts':'正式現況','current_time':'現在時間',
 'recent_dialogue_context':'近期對話的時間與場景','history_instruction':'歷史資料讀法','appearance_instruction':'外觀資料讀法',
 'user_profile':'使用者Profile','decision':'決策核提供的定案','delivery':'語氣核提供的設定','delivery_instruction':'語氣使用規則',
 'story_context':'故事核提供的故事內容','daily_plan':'今天的日程計畫','snapshot':'判讀使用的狀態快照',
 'recent_dialogue':'近期對話原文','past_memory':'檢索記憶／摘要','current_text':'本輪原文','speaker':'本輪發話者',
 'response_context':'回文後觀察的本輪請求與定案','utterance':'話語分類','events':'事件提案','appearance_changes':'外觀變化提案',
 'memory_answer':'回憶回答依據','available_facts':'可引用的紀錄與說法','missing_information':'缺失資訊','uncertainty':'不確定性','answer_scope':'回答範圍','speech_context':'發話背景','spoken_at':'發話時間','time_words':'原話時間指涉','basis':'依據類型','working_updates':'互動筆記更新提案','user_affect':'使用者情緒觀察','appraisals':'事件評估結果','needs_clarification':'是否缺必要資訊',
 'missing_detail':'缺少的資訊','information_scope':'取材範圍','external_request':'公開資訊查詢需求','primary':'主要行為',
 'content':'內容','conditions':'條件','pacing':'互動節奏','intimacy_intention':'親密互動意向','image_requested':'是否要求圖片',
 'response_outline':'回答骨幹','outline':'回答骨幹','activity_stance':'活動意願','chat_engagement':'交談投入',
 'warmth':'溫暖','agency':'主見','expression':'表達強度','teasing':'調侃','personal_disclosure':'自我揭露','length':'長度',
 'levels':'命中級別','sources':'參數來源','inputs':'既有口吻數值','guards':'優先規則','verbal_habits':'說話習慣','surprise_expression':'驚訝表達','source':'來源','accepted':'已採用','rejected':'未採用','changes':'正式狀態變化','proposals':'提案','actor':'行動者',
 'target':'對象','phase':'階段','stage':'階段','evidence':'逐字依據','text':'正文','topic':'話題','ref':'來源引用',
 'query':'查詢詞','mode':'模式','status':'狀態','items':'資料項目','kind':'種類','value':'值','field':'欄位',
 'character_location':'角色地點','user_location':'使用者地點','clothing':'當前衣著','hair':'髮型','body':'固定外觀',
 'activity':'活動','availability':'忙碌狀態','energy':'體力','stress':'壓力','emotions':'情緒','relationship':'關係',
 'traits':'性格','need_weights':'需求權重','invitations':'邀請','promises':'承諾','issues':'待處理問題',
 'story':'角色故事設定','working_memory':'當前互動筆記','goals':'目標','asks_character':'是否在問角色',
 'life_event_input':'生活事件輸入','life_event_disclosure':'生活事件分享確認','conversation_signal':'角色交談意向',
 'story_events':'故事事件提案','story_control':'故事話題控制','target_invitation':'本輪相關邀請'}
VALUES={'user':'使用者','character':'角色','assistant':'角色（API角色assistant）','system':'系統規則',
 'answer':'回答','accept':'接受','conditional_accept':'附條件接受','refuse':'婉拒','defer':'暫緩','clarify':'澄清',
 'invite':'邀請','support':'支持','acknowledge':'承接','suggest':'替代方案','repair':'修復',
 'low':'低','moderate':'中','high':'高','brief':'簡短','normal':'一般','continue':'繼續交談',
 'current_state':'當前狀態','past_events':'過去事件','general':'一般知識','interaction':'當前互動','unknown':'未知'}

def human(value,depth=0):
    pad='  '*depth
    if isinstance(value,dict):
        return '\n'.join(pad+KEYS.get(str(k),str(k))+'：'+ ('\n'+human(v,depth+1) if isinstance(v,(dict,list)) and v else human(v)) for k,v in value.items()) or '無'
    if isinstance(value,list):return '\n'.join(pad+f'{n+1}. '+ ('\n'+human(v,depth+1) if isinstance(v,(dict,list)) else human(v)) for n,v in enumerate(value)) or '無'
    if value is None:return '未提供'
    if type(value) is bool:return '是' if value else '否'
    return VALUES.get(value,value) if isinstance(value,str) else str(value)

def content(text):
    # JSON is rendered as labelled data without dropping any values. Free text
    # (including rules and quoted dialogue) stays verbatim, never translated.
    start=text.find('{')
    if start>=0:
        try:data=json.loads(text[start:])
        except ValueError:return text
        return text[:start]+human(data)
    return text

def model_name(node):
    if not node.get('inputs',{}).get('structured'):return 'Writer'
    if node.get('layer')==2:return 'User Observer'
    if node.get('layer')==5:return 'Response Observer'
    return '背景記憶整理／其他結構化工作'

def describe_flow(node):
    rule=node['rule'];i=node.get('inputs') or {};o=node.get('output') or {}
    if rule in {'model.request','model.failed'}:
        name=model_name(node)
        if rule=='model.failed':name='9B呼叫未完成'
        lines=['執行：本地9B。以下依實際messages順序列出；JSON改為欄位閱讀版，文字內容保留。']
        for n,m in enumerate(i.get('messages',[]),1):
            lines += [f'輸入 {n}｜{VALUES.get(m["role"],m["role"])}\n'+content(m['content'])]
        if rule=='model.failed':lines+=['輸出：沒有成功結果；'+human(o)]
        else:
            raw=o.get('raw','')
            try:result=json.loads(raw) if i.get('structured') else None
            except (ValueError,TypeError):result=None
            lines+=['輸出｜'+ ('欄位閱讀版（模型提案，仍須後端驗證）\n'+human(result) if result is not None else '實際模型文字\n'+raw)]
        if name=='Writer':lines.insert(0,'編輯核 → Writer：以下是實際送出的完整Prompt；故事、定案、口吻等內容按原欄位列出。')
        return name+'｜Input / Output','\n\n'.join(lines)
    if rule=='events.validate_reduce':return '狀態驗證與提交｜Python','輸入｜Observer事件提案\n'+human(i.get('proposals',[]))+'\n\n輸出｜\n'+human(o)
    if rule=='decision.single_selector':
        keys=('primary','secondary','target_event','goal','content','conditions','pacing','intimacy_intention','image_requested','external_use')
        return '決策核｜Python','輸入｜已驗證事件、當前狀態與Observer評估（見本輪前段）。此處省略候選比較與運算。\n\n輸出｜\n'+human({k:o[k] for k in keys if k in o})
    if rule=='delivery.lookup':return '口吻查表｜Python','輸入與命中規則｜\n'+human(i)+'\n\n提供給Writer的口吻｜\n'+human(o)
    if rule=='decision.delivery':return '語氣核｜Python','輸入｜\n'+human({k:v for k,v in i.items() if k!='formulas'})+'\n\n輸出｜\n'+human(o)
    if rule=='decision.expression_brief':return '回答骨幹編譯｜Python','輸入｜\n'+human(i)+'\n\n輸出｜\n'+human(o)
    if rule=='context.source_priority':return '相關來源容量配置｜Python',human(o)+'\n沒有增加總context或模型呼叫。'
    if rule=='story.index':return '場景與話題索引｜Python','輸出｜'+human(o)+'\n只建立來源連結，不更新人物或場景。'
    if rule=='memory.answer_brief':return '故事核 → 回答骨幹｜記憶交接','提供給Writer｜\n'+human(o)+'\n原始來源見本輪編輯核選定來源；此處不另推理或重選行為。'
    if rule=='memory.packet':return '統一記憶 → Writer｜Python','來源與取捨｜\n'+human(i)+'\n\n實際記憶閱讀版｜\n'+(o.get('text') or '本輪無需補充記憶。')
    if rule in {'memory.pending_nonblocking','memory.response_rebase','memory.task_timeout','memory.vector_warm'}:return '記憶整理狀態｜Python',human(o)+'\n'+node.get('reason','')
    if rule=='memory.recall':return '記憶檢索｜Python','輸入｜\n'+human({'query':i.get('query','')})+'\n\n輸出｜候選資料，尚不代表全部交給Writer\n'+human(o.get('items',[]))
    if rule in {'working.updated','story.events'}:return ('互動筆記更新' if rule=='working.updated' else '故事事件整理')+'｜Python','輸入｜同一次Observer的更新提案，以上方模型輸出為準。\n\n輸出｜\n'+human(o)
    if rule=='context.selected_sources':return '編輯核選定來源｜Python','用於階段：'+human(i.get('stage',''))+'\n\n輸出｜實際選用的完整往返、記憶與故事來源（仍須經Builder投影）\n'+human(o)
    if rule=='story.reading':return '故事核 → 編輯核｜Python','輸入｜本輪選定來源見「編輯核選定來源」；現況生活事件依正式狀態組合。舊trace缺少來源清冊時不補猜。\n\n輸出｜實際故事段落\n'+(o.get('text') or '本輪未帶故事段落。')
    if rule=='proactive.continuation':return '故事核 → 主動接續交接｜Python','提供給Writer｜\n'+human(o)+'\n重提話題保留；這是發話紀錄，不判定事件完成或使用者位置。'
    if rule=='working.reading':return '互動筆記 → 編輯核｜Python','輸出｜實際有效筆記\n'+(o.get('text') or '無')
    if rule in {'context.prepared','context.output_reserve','context.capacity_failed','response.scheduled','telegram.response_deferred','story.background_deferred','debug.finished','writer.consistency'}:
        from .telegram_debug import describe
        return describe(node)
    return None
