"""Build-time occasion overlays; no runtime generation or completed events."""
import json
from pathlib import Path
PATH=Path(__file__).resolve().parents[1]/'content/annual-life/occasions.json'
def apply_occasions(day,activity):
 config=json.loads(PATH.read_text());key=day['date'][5:];schedule=day['activities']
 for trip in config['trips']:
  if key not in trip['dates']:continue
  index=trip['dates'].index(key);first=index==0;last=index==len(trip['dates'])-1;overnight=trip['bedroom'] is not None
  start_room='青葉莊園臥室' if first else trip['bedroom'];end_room='青葉莊園臥室' if last else trip['bedroom']
  dining=trip['dining'];area=trip['area'];outing=trip['outing']
  # A day trip uses a carried lunch in the village instead of inventing a cafe.
  schedule=[activity('00:00',start_room,'夜間睡眠','sleep',look='sleep',outcome='休息'),
   activity('07:00','青葉莊園浴室' if first else start_room,'起床後盥洗與整理旅行用品',look='wash'),
   activity('08:00','青葉莊園餐廳' if first else dining,'享用早餐與確認今日旅行安排'),
   activity('10:00',area,'村莊漫步與觀察街景' if area!='白帆小屋附近海岸' else '沿公開海岸步道散步與觀察海景','free',True,outcome=trip['focus']),
   activity('12:30',dining or area,'享用午餐與休息' if dining else '在村莊合適休憩處享用自備午餐'),
   activity('14:30','青葉莊園門廳' if last and overnight else outing,'返家後整理旅行行李' if last and overnight else '逛糖果店與挑選小份點心' if outing=='蜜糖屋糖果店' else '慢行與記下旅途中有趣的細節','free',True),
   activity('18:00','青葉莊園餐廳' if last else dining,'晚餐與休息'),
   activity('20:00','青葉莊園起居室' if last else end_room,'晚間閱讀與整理旅行隨筆'),
   activity('22:00',end_room,'就寢與休息','sleep',look='sleep',outcome='休息')]
  day.update(activities=schedule,title=trip['title']+f'・第{index+1}天',focus=trip['focus'],trip={'id':trip['id'],'day':index+1,'total_days':len(trip['dates']),'planning_only':True,'user_accompanies':False})
  # The closing night's sleep must agree with the next day's midnight plan.
  day['event_candidates']=[{'at':'10:00','location':area,'kind':'possible_observation','premise':'考慮把旅途中一個有趣的觀察記成簡短隨筆。','boundary':'只談實際進展；不預設使用者同行，不編造購物完成、天氣或原作人物會面。','status':'draft'},
   {'at':'14:30','location':schedule[5]['location'],'kind':'possible_choice','premise':'在多走一段路與留時間休息之間作選擇。','boundary':'可以提早休息；草案不是已發生事實。','status':'draft'}]
  break
 birthdays=[b for b in config['birthdays'] if b['month_day']==key]
 if birthdays:
  day['birthdays']=birthdays;names='與'.join(b['label'] for b in birthdays);day['title']+='・'+names+'生日'
  # When travelling, celebrate where the character actually stays that evening.
  celebration_place=day['activities'][7]['location']
  day['activities'][7]=activity('20:00',celebration_place,names+'生日：留一段小慶祝與寫祝福的時間','free',True,outcome='保留可以依對話調整的生日安排')
  day['event_candidates'][1]={'at':'20:00','location':celebration_place,'kind':'birthday_plan','premise':names+'生日：考慮準備一份簡單祝福或小點心。','boundary':'不預設使用者到場、收到禮物或回應；不自動增齡或改寫關係。','status':'draft'}
 return day
