"""Per-person departures and arrivals. No language classifier or action selector."""
from copy import deepcopy
import re

# Direction-only legacy values are not a confirmed current location.
LEGACY_DIRECTION=re.compile(r'^(?:前往|正在前往|出發去|出發到|趕往)\s*(.+?)\s*[。！!]*$')
RULES='''移動必須獨立於問句提取：一句先報到、再問問題，utterance仍可question，但events不能漏掉抵達。
move的field：departure=已實際出發／在途中，location=已抵達的所在地，relative_position/distance=同地靠近遠離；「到了」不是相對站位。純邀請／打算去仍是proposed，不提交出發或抵達。
value只寫地名，不能寫「前往某地」等整句。無地名的抵達可用@journey指該人物snapshot.scene.journeys的目的地；明確到角色所在地用@character。沒有唯一依據則needs_clarification=true，不猜。
actor是移動的人；target=scene只用明確共同移動。已確認共同旅程中，使用者報抵達同目的地，後端會更新同旅程同行者；不同旅程不連動。使用者單獨抵達不代表角色也抵達。角色回文不能替使用者建立出發或抵達。
人物範例（優先）：user說「到圖書館囉，要先去哪一區？」→ actor=user,target=user,field=location,value=圖書館；不得移動character。user說「我也到了，是很困難的工作嗎？」且character在圖書館→ actor=user,target=user,field=location,value=@character；不可沿用user原本的公園。user說「我們到圖書館了」→ actor=user,target=scene,field=location,value=圖書館。user說「我們出發去圖書館了」→ actor=user,target=scene,field=departure,value=圖書館。單獨移動target用actor本人，絕不因雙方正在聊天就選scene。'''

def migrate_legacy(state,trace=None):
 scene=state['scene'];old=scene.get('user_location','')
 if not isinstance(old,str):return False
 match=LEGACY_DIRECTION.fullmatch(old)
 if not match or scene.get('journeys',{}).get('user'):return False
 # Only the old explicit directional value is retained. Origin/start time and
 # character participation are unknown; never invent either from the calendar.
 scene.setdefault('journeys',{})['user']={'destination':match.group(1),'origin':'','status':'moving',
   'started_at':'','group':'','source':'legacy_direction','legacy_value':old}
 scene['user_location']=''
 scene.get('positions',{}).pop('user',None)
 if trace:trace.add(1,'scene.legacy_direction',{'previous_user_location':old},deepcopy(scene['journeys']['user']),
   '舊方向文字轉為使用者途中狀態；不補出發地／時間／同行者或抵達。')
 return True

def singular_self_report(text):
 # Evidence may quote a whole clarification, not just its movement clause.
 # Match the self-report inside prose, without granting quoted/conditional
 # clauses authority to move anyone. This checks actor scope, not destination.
 match=re.search(r'我(?![們们])(?:已經|已经|現在|现在|也|剛剛|刚刚|剛|刚)?(?:到|抵達|抵达|來到|来到|出發|出发|前往|走到|回到)',text)
 if not match:match=re.search(r"\bI(?:['’]ve| have)? (?:already |just )?(?:arrived|reached|got to|am at|left|headed)\b",text,re.I)
 if not match:return False
 prefix=re.split(r'[，,。；;！!？?\n]',text[:match.start()])[-1]
 if re.search(r'不是|並非|并非|沒有|没有|如果|假[設设]|假如|要是|[他她]說|[他她]说|[「『"“]|\b(?:if|suppose|said)\b',prefix,re.I):return False
 return True

def resolve(state,event):
 if event.field not in {'location','departure'}:return ''
 from .scene_background import same_location
 if not isinstance(event.value,str):return '移動地點必須是字串'
 scene=state['scene'];journey=scene.get('journeys',{}).get(event.actor,{})
 if not isinstance(event.evidence,str):return '移動證據必須是字串'
 # A literal singular self-report is not authority to relocate both people.
 # Existing confirmed shared journeys may still expand arrival in reduce().
 singular=singular_self_report(event.evidence.strip())
 if event.source=='user' and singular:
  if event.actor!='user':return '單人自述的移動主體必須是使用者，不能改寫角色地點'
  event.target='user'
 if event.source=='user' and event.target=='scene' and not re.search(r'我們|我们|咱們|咱们|我(?:跟|和|與|与)[你妳]|[你妳]我|兩人|两人|兩個人|两个人|一起|一同|\bwe\b|\bboth\b|\btogether\b|私たち|私達|二人|一緒',event.evidence,re.I):
  return '共同移動缺少明確同行來源，不能只因target=scene就移動角色'
 if event.value=='@journey':
  if event.field!='location' or journey.get('status')!='moving' or not journey.get('destination'):
   return '沒有此人物唯一有效的途中目的地，不能推定抵達'
  event.value=journey['destination']
 elif event.value=='@character':
  location=scene.get('character_location') or scene.get('location')
  if event.field!='location' or event.actor!='user' or not location or location in {'未確認','unknown'} or LEGACY_DIRECTION.fullmatch(location):
   return '角色所在地不足以解析本次抵達'
  # If travelling somewhere else, bare arrival is ambiguous. Explicit named
  # destinations remain usable as an intentional change of plan.
  if journey.get('status')=='moving' and not same_location(state,journey.get('destination'),location):
   return '使用者途中目的地與角色所在地不同；請明確確認抵達何處'
  event.value=location
 if not event.value.strip() or event.value.startswith('@') or LEGACY_DIRECTION.fullmatch(event.value):
  return '移動目的地必須是地名；出發用departure，不把方向句當所在地'
 from .scene_background import canonical_location
 canonical=canonical_location(state,event.value)
 if canonical:event.value=canonical
 elif state.get('scene_background_world') and (event.actor=='character' or event.target=='scene' or
      (event.field=='location' and journey.get('group') and any(
       person=='character' and trip.get('status')=='moving' and trip.get('group')==journey['group']
       and same_location(state,trip.get('destination'),event.value)
       for person,trip in scene.get('journeys',{}).items()))):
  return '角色目的地尚未登錄或有歧義，不能直接用原文字串建立正式場景'
 return ''

def reduce(state,event):
 from .scene_background import same_location
 scene=state['scene'];trips=scene.setdefault('journeys',{})
 participants=['user','character'] if event.target=='scene' and event.source=='user' else [event.actor]
 own=trips.get(event.actor,{})
 # Only an existing, still-active shared departure enables implicit joint
 # arrival. Assistant narration cannot complete the user's leg.
 if event.field=='location' and event.source=='user' and own.get('group') and own.get('status')=='moving' and same_location(state,own.get('destination'),event.value):
  for person,trip in trips.items():
   if trip.get('group')==own['group'] and trip.get('status')=='moving' and same_location(state,trip.get('destination'),event.value) and person not in participants:
    participants.append(person)
 if 'character' in participants:state.pop('_scene_handoff',None)
 if event.field=='departure':
  group=event.id if len(participants)>1 else ''
  for person in participants:
   scene.get('positions',{}).pop(person,None)
   origin=scene.get('character_location') or scene.get('location','') if person=='character' else scene.get('user_location','')
   trips[person]={'destination':event.value,'origin':origin,'status':'moving','started_at':event.timestamp,'group':group,'source':event.id}
  if 'character' in participants:
   scene['pending']=None;scene['status']='moving';state['activity']='前往'+event.value
 else:
  for person in participants:
   trips.pop(person,None);scene.get('positions',{}).pop(person,None)
   if person=='character':
    scene.update(location=event.value,character_location=event.value,pending=None,status='active')
    from .scene_activity import is_travel
    if is_travel(state.get('activity','')):state['activity']=''
   else:scene['user_location']=event.value
  if not trips:scene.pop('journeys',None)
 state['presentation']={}

def leave_schedule(state):
 # A calendar move is character-only and explicitly breaks that person's
 # previous trip; it must not drag the user or leave a stale shared leg.
 trips=state['scene'].get('journeys',{})
 trips.pop('character',None)
 if not trips:state['scene'].pop('journeys',None)



def departure_notice(state, pending):
 destination=pending['location']
 paused=pending.get('departure_context',{}).get('idle_minutes',0)>=10
 if state['language']=='EN':
  return ("We’ve paused our chat for a while, so I’m heading to "+destination+" now. We can catch up later."
          if paused else "I’m heading to "+destination+" now.")
 if state['language']=='JP':return ('少し会話が空いたから、先に' if paused else 'これから')+destination+'へ向かうね。'+('またあとで話そう。' if paused else '')
 return ('剛才暫時沒聊，我先去' if paused else '我出發去')+destination+'了。'+('晚點再聊。' if paused else '')

def pending_scene_handoff(state):
 """A delivered foreground/casual reply with the visible header consumes it."""
 h=state.get('_scene_handoff')
 if not h or not h.get('origin') or not h.get('destination'):return ''
 from .scene_background import same_location
 if same_location(state,h['origin'],h['destination']):return ''
 if (state['scene'].get('character_location') or state['scene'].get('location'))!=h['destination']:return ''
 # Scene notices do not display this header. Casual delivery does display it,
 # so it consumes the visible bridge without claiming a human response.
 for message in state.get('history',[])[h['after_history_index']:]:
  background=bool(message.get('background_kind') or message.get('turn_id','').startswith('background:'))
  if message.get('role')=='assistant' and (not background or message.get('background_kind')=='casual'):
   return None
 return h

def scene_handoff_note(state):
 h=pending_scene_handoff(state)
 if not h:return ''
 disclosure='當時沒有送出換場通知；不可說成已告知。' if h.get('notice_status')=='silent' else '換場通知已送達。' if h.get('notice_status')=='delivered' else '不推定使用者收到通知。'
 return (f"可自然交代接續：角色已從{h['origin']}移到{h['destination']}，這是已完成的換場。"
         +disclosure+'不播報日期時刻或閒置分鐘，不說成現在才出發或抵達。不猜使用者未回覆原因，不責怪，不假設使用者同行或原話題已完成。然後接續本輪內容：')
