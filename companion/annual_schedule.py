"""Authored calendar lookup only. A plan never establishes completed events."""
from copy import deepcopy
from datetime import date,timedelta
from functools import lru_cache
import json
from pathlib import Path
from .life import parse_daily_plan

PATH=Path(__file__).with_name('data')/'annual_life.json'
@lru_cache(maxsize=1)
def load_calendar():
 data=json.loads(PATH.read_text())
 if data.get('version')!=1 or data.get('world')!='original_world' or len(data.get('days',[]))!=365:raise ValueError('年度行程資料無效')
 days=data['days'];start=date.fromisoformat(data['start_date']);keys=set()
 for n,row in enumerate(days):
  if row['date']!=(start+timedelta(days=n)).isoformat() or not row.get('planning_only'):raise ValueError('年度行程日期或草案標記無效')
  key=row['date'][5:]
  if key in keys:raise ValueError('年度行程日期重複')
  keys.add(key)
  items=parse_daily_plan(json.dumps({'activities':row['activities']},ensure_ascii=False))
  if len(items)!=9 or items[0]['start']!='00:00' or items[0]['availability']!='sleep' or items[-1]['start']!='22:00' or items[-1]['availability']!='sleep':raise ValueError('年度行程睡眠銜接無效')
 return {r['date'][5:]:r for r in days}

@lru_cache(maxsize=1)
def weekend_routes():
 routes=json.loads(PATH.with_name('weekend_life.json').read_text())
 if len(routes)!=3:raise ValueError('週末行程資料無效')
 for row in routes:
  if len(parse_daily_plan(json.dumps({'activities':row['activities']},ensure_ascii=False)))!=9:raise ValueError('週末行程不完整')
 return routes

def apply_actual_weekday(row,now):
 # Explicit trips and holidays keep their own complete timeline.
 if row.get('trip') or row.get('holiday'):return
 birthday_evening=deepcopy(row['activities'][7]) if row.get('birthdays') else None
 birthday_draft=deepcopy(row['event_candidates'][1]) if row.get('birthdays') else None
 if now.weekday()>=5:
  index=((now.day-1)//7)%3 if now.weekday()==5 else (2 if ((now.day-1)//7)%2==0 else 1)
  route=weekend_routes()[index]
  row.update(title=route['title']+'・'+row['focus'],activities=deepcopy(route['activities']),event_candidates=deepcopy(route['event_candidates']),weekend={'route':route['id'],'planning_only':True})
 elif 'weekday_activities' in row:
  row.update(title=row['weekday_title'],activities=deepcopy(row['weekday_activities']),event_candidates=deepcopy(row['weekday_event_candidates']))
  row.pop('weekend',None)
 if birthday_evening:
  row['activities'][7]=birthday_evening;row['event_candidates'][1]=birthday_draft
  row['title']+='・'+'與'.join(b['label'] for b in row['birthdays'])+'生日'

def lookup_day(now,world='original_world'):
 if world!='original_world':raise ValueError('此年度行程只適用星港原創幻想世界角色')
 rows=load_calendar();key=now.strftime('%m-%d');leap=key=='02-29'
 row=deepcopy(rows['02-28' if leap else key]);row['source_date']=row['date'];row['date']=now.date().isoformat()
 row['weekday']='一二三四五六日'[now.weekday()];row['leap_day_extension']=leap
 apply_actual_weekday(row,now)
 if leap:row['title']='閏日留白：'+row['title']
 return row

def install_day(state,now):
 row=lookup_day(now,state.get('scene_background_world','original_world'))
 items=parse_daily_plan(json.dumps({'activities':row['activities']},ensure_ascii=False));midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
 state['schedule']=[dict(i,arrival_activity='',at=(midnight+timedelta(minutes=i['start_minute'])).isoformat(),plan_source='annual_table') for i in items]
 state.update(life_plan_day=now.date().isoformat(),life_plan_retry='',life_plan_attempt_version='annual-table-v1',life_plan_contract='annual-table-v1')
 state.pop('life_plan_failure',None)
 state['life_calendar_day']={k:row[k] for k in ('date','source_date','title','focus','week','planning_only','event_candidates','leap_day_extension')}
 for key in ('trip','birthdays','weekend','holiday'):
  if key in row:state['life_calendar_day'][key]=deepcopy(row[key])
 return row
