"""Narrow explicit self-description rules, alongside the existing Observer.

No guessed preferences, third-party statements, implicit identity or fictional facts.
"""
import re

def profile_proposals(text,state):
 result=[]
 if re.search(r'密碼|憑證|token|api[_ -]?key',text,re.I):return result
 # Explicit addressing requests only, not arbitrary fictional names in a story.
 name=re.match(r'\s*(?:請叫我|叫我|我的名字是|我叫)([^，。！？\n,!?]{1,24})',text)
 if name and (text.lstrip().startswith(('請叫我','叫我','我的名字是')) or re.search(r'稱呼|叫我',text[name.end():])):
  value=name.group(1).strip();result.append(('name',value,name.group(0).strip()))
 preference=re.match(r'\s*(?:更正(?:一下)?[，,：:]?\s*)?我(?:現在|目前)?(?:最)?喜歡(?:的[^，。！？\n,!?]{1,12}是|喝|吃)?([^，。！？\n,!?]{1,80})',text)
 if preference:
  value=preference.group(1).strip()
  if not re.search(r'如果|假設|可能|也許|不是|不喜歡|不再',value):result.append(('preference',value,preference.group(0).strip()))
 proposals=[]
 for field,value,evidence in result[:2]:
  # A question supplies no concrete profile value, even when it starts with 我.
  if re.search(r'什麼|甚麼|哪(?:個|種|些|裡)|誰|幾|是否|是不是|[嗎呢]$|\b(?:what|which|who)\b',value,re.I):continue
  existing=[p for p in state.get('user_profile',[]) if p.get('category')==field]
  previous=None
  if field=='name' and len(existing)==1:previous=existing[0]
  elif re.search(r'更正|改成|不再|不是',text):
   candidates=[p for p in existing if p.get('value') and re.search(r'(?:不是|不再(?:喜歡)?)[^，。！？\n,!?]*'+re.escape(p['value']),text)]
   if len(candidates)==1:previous=candidates[0]
  proposals.append({'kind':'profile_update' if previous else 'profile_add','actor':'user','target':'user','stage':'observed','field':field,'value':value,'evidence':text if previous else evidence,'strength':1,'ref':str(previous['id']) if previous else ''})
 return proposals
