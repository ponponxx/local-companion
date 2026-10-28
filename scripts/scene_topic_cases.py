"""Predeclared neutral questions for scene/topic links. No model-authored gold."""
from copy import deepcopy
from datetime import datetime,timedelta
from scripts.working_memory_cases import base
from companion.story_events import annotate
from companion.scene_topic_index import MODE

def cases(filler_count=7):
 rows=[]
 def setup():
  s=base();s['scene'].update(user_location='');s['memory_index_mode']=MODE;return s
 def arrive(s,id,where,hour,day='2026-10-20'):
  old=s['scene']['character_location'];at=f'{day}T{hour}:00+08:00'
  s['scene_history'].append(dict(id=id,at=at,phase='arrived',actor='character',before={'character_location':old},after={'character_location':where},source='synthetic',history_index=len(s['history'])))
  s['scene'].update(location=where,character_location=where);s['now']=s['updated_at']=at
 def say(s,text,reply,topic='月報',actor='user',stage='completed'):
  for role,content in [('user',text),('assistant',reply)]:
   m=dict(role=role,content=content,at=s['now'],turn_id='fixture-'+str(len(s['history'])),context={'scene':{'character_location':s['scene']['character_location'],'user_location':''}})
   s['history'].append(m);annotate(m,{'topic':topic,'story_events':[dict(actor=actor,target='none',stage=stage,action=text,evidence=text)] if role=='user' else []})
 def add(id,s,q,expect):
  for i in range(filler_count):say(s,'我先看看窗外的樹影與石徑，這只是隨口描述眼前的環境，沒有說原來的問題解決，也沒有建立任何新約定。','嗯，先看一會兒景色也好，先前說過的事不會因此變成另一個人的經歷。','當前內容')
  s['summary_through']=max(0,len(s['history'])-8)
  rows.append(dict(id=id,state=s,question=q,expected=expect))
 s=setup();arrive(s,'a','石板街','09:00');say(s,'主管請我修改藍色封面的月報。','你說的是藍色封面的月報。');arrive(s,'b','茶館','10:00');say(s,'回到剛才的月報，我打算明天才改。','你打算明天修改，現在還沒完成。',stage='planned');add('topic-across-scenes',s,'我的主管要我修改什麼？我說何時才改？','使用者的藍色封面月報，使用者打算明天才修改，未完成。')
 s=setup();arrive(s,'a','石板街','09:00');say(s,'昨天我的主管責備我，說月報缺了兩頁。','原來你昨天因為月報缺兩頁被主管責備。');arrive(s,'b','茶館','10:00');add('user-life-not-character-location',s,'昨天被主管責備的是誰？妳知道事情發生在哪裡嗎？','是使用者Alex，不是角色；未提供事件發生地，不能推成石板街。')
 s=setup();arrive(s,'a','石板街','09:00');say(s,'我想買一枝綠色羽毛筆。','你想買綠色羽毛筆。','挑選文具',stage='planned');arrive(s,'b','茶館','10:00');say(s,'我想喝紅茶。','你想喝紅茶。','喝茶',stage='planned');arrive(s,'c','石板街','11:00');say(s,'這次我想買藍色筆記本。','這次是藍色筆記本。','挑選文具',stage='planned');add('previous-visit-not-current',s,'上次在石板街時，我說想買什麼？不是這一次。','上一回是綠色羽毛筆，不是這次的藍色筆記本，不能宣稱已購買。')
 s=setup();arrive(s,'a','石板街','09:00');say(s,'主管要我補上月報的附件。','我聽到你說主管要補月報附件。');arrive(s,'b','茶館','10:00');say(s,'我想換成熱可可。','好，你想喝熱可可。','喝茶',stage='planned');add('scene-to-topic',s,'妳在石板街的時候，我告訴妳什麼事？','使用者提到主管要求補月報附件，不是茶館的熱可可。')
 s=setup();arrive(s,'a','花園','09:00');say(s,'我打算星期五才寄出報告，還沒寄。','你計畫星期五寄，目前尚未寄出。',stage='planned');arrive(s,'b','書房','10:00');add('plan-not-completion',s,'我已經寄出報告了嗎？','未寄出，只計畫星期五寄出，不因換場變完成。')
 s=setup();arrive(s,'a','花園','09:00');say(s,'我自己的主管叫林先生，妳的主管我不知道。','你工作的主管是林先生，我的主管沒有提供。','主管稱呼');arrive(s,'b','書房','10:00');add('actor-owner',s,'林先生是誰的主管？','林先生是使用者Alex的主管，不是角色主管。')
 s=setup();arrive(s,'a','花園','23:00');say(s,'我的面試排在明天早上十點，現在還沒開始。','你明天早上十點要面試，還沒開始。','面試',stage='planned');s['now']=s['updated_at']='2026-10-21T00:10:00+08:00';add('midnight-source-clock',s,'我的面試是今天還是明天？幾點？','發話是10/20晚間，現在10/21凌晨，所以今天10點，尚未開始。')
 s=setup();s['scene'].update(location='石板街',character_location='石板街');say(s,'我的月報需要補三張圖。','你要補三張圖。');add('unknown-user-position',s,'妳知道我談月報時，人也在石板街嗎？','不知道使用者位置；角色在石板街不代表使用者也在。')
 s=setup();arrive(s,'a','石板街','09:00');say(s,'我喜歡茉莉花茶。','你喜歡茉莉花茶。','茶口味');say(s,'我主管要補月報數據。','你主管要求補月報數據。');add('two-topics-one-visit',s,'我在妳逛石板街時，提過喜歡哪種茶？','使用者喜歡茉莉花茶，不回答月報。')
 s=setup();arrive(s,'a','花園','09:00');say(s,'剛才我說主管已回覆是口誤，現在其實還沒回覆。','收到，更正為你的主管目前還沒有回覆。','主管回覆',stage='negated');arrive(s,'b','書房','10:00');add('explicit-correction',s,'我的主管回覆了沒有？','依明確更正，目前還沒有回覆。')
 s=setup();arrive(s,'a','花園','09:00');say(s,'主管建議我問一下附件格式。','我問你：你想先確認附件格式嗎？');arrive(s,'b','書房','10:00');add('who-asked-whom',s,'剛才是誰問誰要不要先確認附件格式？','角色米拉問使用者Alex，不把提問者交換。')
 s=setup();arrive(s,'a','花園','09:00');say(s,'我這次被要求修改月報的封面。','這次是月報封面。');arrive(s,'b','書房','10:00');say(s,'先換話題談書。','好，談書吧。','讀書');arrive(s,'c','花園','11:00');say(s,'另一次主管叫我補月報附件。','另一次是補附件。');arrive(s,'d','茶館','12:00');add('ambiguous-visit',s,'妳在花園時，我主管到底要求改哪裡？','有兩次花園對話：一次改封面、另一次補附件；不能任意合成同一次，可列兩次或澄清。')
 return rows
