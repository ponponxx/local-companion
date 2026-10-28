const $=id=>document.getElementById(id);
const labels={identity:'身份',character:'角色',user:'使用者',actors_are_distinct:'人物分開',character_definition:'角色背景',background:'背景',current_time:'本輪時間',current_facts:'正式現況',scene:'場景',character_location:'角色位置',user_location:'使用者位置',activity:'目前活動',availability:'可投入程度',mood:'心情',energy_level:'體力',stress_level:'壓力',appearance:'目前外觀',clothing:'目前穿著',decision:'行為定案',primary:'主要行為',conditions:'條件',content:'內容',response_outline:'回答骨幹',pacing:'節奏',intimacy_intention:'親密意圖',image_requested:'是否要求圖片',activity_stance:'活動立場',chat_engagement:'交談投入',delivery:'口吻',warmth:'溫暖程度',agency:'主見',expression:'表達方式',teasing:'調侃',personal_disclosure:'自我揭露',surprise_expression:'驚訝表達',length:'回覆長度',verbal_habits:'口癖',delivery_instruction:'口吻限制',daily_plan:'當日行程',status:'狀態',upcoming:'接下來的安排',time:'時間',location:'地點',meaning:'資料界線',story_context:'故事與記憶',user_profile:'使用者資料',proactive_story:'本次主動故事',proactive_continuation:'主動話題接續',intent:'用意',prior_exchange:'先前互動',instruction:'使用規則',recent_dialogue_context:'近期對話背景',history_instruction:'歷史資料規則'};
Object.assign(labels,{snapshot:'判讀起點快照',current_text:'本輪原文',history:'近期歷史',speaker:'發話者',utterance:'話語判讀',act:'話語行為',purpose:'用途',events:'事件',working_updates:'互動記憶更新',needs_clarification:'是否需要澄清',information_scope:'取材範圍',appraisals:'情緒與需求評估',story_events:'故事事件',topic:'話題',evidence:'逐字證據',actor:'行動者',target:'對象',field:'欄位',value:'內容值',stage:'階段',missing_detail:'缺少資訊',model:'模型',max_tokens:'輸出額度',temperature:'取樣溫度',response_schema:'輸出格式限制',enable_thinking:'是否啟用思考'});
let snapshot=null,etag='',mode='read',activeDigest='',busy=false,detail=null,stage='observer_input',turnTag='',loadedTurn=null;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function toast(text){$('toast').textContent=text;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',2200);}
function splitContent(text){
  // Parse a complete JSON suffix only. Never globally replace backslashes:
  // literal paths, quoted strings and malformed content must remain literal.
  const candidates=[0,...[...text.matchAll(/\n[ \t]*(?=[{\[])/g)].map(m=>m.index+1)];
  for(const i of candidates){const tail=text.slice(i).trim();if(!/^[{\[]/.test(tail))continue;try{return {prefix:text.slice(0,i).trimEnd(),data:JSON.parse(tail)};}catch{}}
  return {prefix:text,data:null};
}
function fieldLabel(key){const box=el('div',undefined,'field-label');box.append(el('span',labels[key]||key));if(labels[key])box.append(el('span',key,'key'));return box;}
function valueNode(value,depth=0){
  if(depth>40)return el('pre',JSON.stringify(value,null,2));
  if(value===null)return el('span','null','empty-value');
  if(typeof value!=='object')return el('div',value===''?'""（空字串）':String(value),'value');
  if(Array.isArray(value)){
    if(!value.length)return el('span','[]（空陣列）','empty-value');
    const box=el('div');value.forEach((v,i)=>{const item=el('div',undefined,'array-item');item.append(el('div',`第 ${i+1} 項`,'item-number'),valueNode(v,depth+1));box.append(item);});return box;
  }
  if(!Object.keys(value).length)return el('span','{}（空物件）','empty-value');
  const box=el('div');Object.entries(value).forEach(([key,v])=>{const row=el('div',undefined,'row');row.append(fieldLabel(key),valueNode(v,depth+1));box.append(row);});return box;
}
function card(title,key,body,id){const box=el('section',undefined,'card');box.id=id;const head=el('button',undefined,'card-title');head.type='button';head.setAttribute('aria-expanded','true');head.append(el('span',title),el('span',key||'','key'));const content=el('div',undefined,'card-body');content.append(body);head.onclick=()=>{content.hidden=!content.hidden;head.setAttribute('aria-expanded',String(!content.hidden));};box.append(head,content);return box;}
function nav(text,id,sub=false){const link=el('a',text,sub?'sub':'');link.href='#'+id;$('nav').append(link);}
function chosenCall(calls){return calls?.findLast(c=>c.status==='completed')||calls?.at(-1);}
function selected(){
  if($('source').value!=='turn')return snapshot?.[$('source').value];
  const observer=detail?.observers?.[Number($('attempt').value)];
  if(stage==='writer_input')return chosenCall(detail?.writers)?.input;
  return observer?.[stage==='observer_input'?'input':'output'];
}
function currentSignature(){return [$('source').value,stage,loadedTurn,selected()?.digest||''].join(':');}
function controls(){const isTurn=$('source').value==='turn';$('turn-tools').hidden=!isTurn;$('stage-tabs').hidden=!isTurn;}
function render(){
  controls();const item=selected();activeDigest=currentSignature();$('content').replaceChildren();$('nav').replaceChildren();
  if($('source').value==='turn'&&detail){
    $('content').append(card('本輪使用者輸入','同一筆對話紀錄',el('pre',detail.run.text||'（無文字原文）'),'turn-input'));
    nav('本輪使用者輸入','turn-input');
  }
  if(!item?.raw){const text=$('source').value==='turn'?(stage==='writer_input'?'本輪尚無 Writer 請求，可能尚未執行或已在前面停止。':stage==='observer_output'?'這次判讀尚無封存輸出；請查看輸入頁的呼叫狀態。':'本輪沒有封存的 User Observer 模型請求；不會借用其他輪次補上。'):'目前尚無已保存的 Prompt。先完成一輪對話後，這裡會自動更新。';$('content').append(el('div',text,'empty'));return;}
  const title={image:'圖片生成 Prompt',request:'模型輸入',output:'判讀原始輸出',text:'文字 messages'}[item.kind]||'封存資料';
  $('content').append(el('div',`${title} · ${item.raw.length.toLocaleString()} 個封存字元 · 顯示原順序、不省略欄位`,'stats'));
  if(item.status){const info=`呼叫狀態：${item.status}${item.seconds!=null?' · '+item.seconds+' 秒':''}${item.kind==='request'?' · '+(item.prepared?'已保存實際組裝的請求（不代表伺服器收件證據）':'只有預定請求，尚無 prepared 紀錄'):''}`;$('content').append(el('div',info,'stats'));}
  if(mode==='raw'||item.kind==='image'){$('content').append(card(mode==='raw'?'原始封存':'圖片 Prompt','',el('pre',item.raw),'raw'));nav('完整內容','raw');return;}
  let decoded;try{decoded=JSON.parse(item.raw);}catch{decoded=null;}
  if(item.kind==='output'){
    $('content').append(card('首次判讀輸出','模型原始結果，非後端修改後的狀態',mode==='json'||decoded===null?el('pre',decoded===null?item.raw:JSON.stringify(decoded,null,2)):valueNode(decoded),'observer-result'));nav('判讀輸出','observer-result');return;
  }
  let messages=Array.isArray(decoded)?decoded:decoded?.messages;
  if(!Array.isArray(messages)){$('content').append(card('原文（無法解析為 messages）','',el('pre',item.raw),'raw'));nav('原文','raw');return;}
  messages.forEach((message,index)=>{
    const id='message-'+index;const section=el('section',undefined,'message');section.id=id;
    if(!message||typeof message!=='object') {section.append(card('訊息 '+(index+1),'',valueNode(message),id+'-value'));$('content').append(section);nav('訊息 '+(index+1),id);return;}
    const role=message.role||'unknown';const roleNames={system:'系統規則',developer:'開發規則',user:'使用者／控制訊息',assistant:'角色歷史回覆',tool:'工具結果'};
    const head=el('div',undefined,'message-head');head.append(el('span',String(role),'badge'),el('h2',`訊息 ${index+1} · ${roleNames[role]||role}`));section.append(head);nav(`${index+1} · ${role}`,id);
    if(typeof message.content!=='string')section.append(card('內容','content',valueNode(message.content),id+'-content'));
    else {
      const split=splitContent(message.content);
      if(split.prefix){const instructions=['system','developer'].includes(role);const display=mode==='read'&&instructions?split.prefix.replace(/([。！？])(?=\S)/g,'$1\n'):split.prefix;section.append(card(instructions?'指令':'原文內容','content',el('pre',display),id+'-instructions'));nav(instructions?'指令':'原文內容',id+'-instructions',true);}
      if(split.data!==null){
        if(mode==='json'||Array.isArray(split.data)){section.append(card('結構化資料','content（已解開外層字串）',el('pre',JSON.stringify(split.data,null,2)),id+'-data'));nav('結構化資料',id+'-data',true);}
        else Object.entries(split.data).forEach(([key,v],i)=>{const subid=id+'-field-'+i;section.append(card(labels[key]||key,key,valueNode(v),subid));nav(labels[key]||key,subid,true);});
      }
    }
    const extra=Object.fromEntries(Object.entries(message).filter(([key])=>!['role','content'].includes(key)));
    if(Object.keys(extra).length)section.append(card('其他訊息欄位','',valueNode(extra),id+'-extra'));
    $('content').append(section);
  });
  if(decoded&&!Array.isArray(decoded)&&Array.isArray(decoded.messages)){
    const config=Object.fromEntries(Object.entries(decoded).filter(([key])=>key!=='messages'));
    if(Object.keys(config).length){$('content').append(card('呼叫設定與輸出格式','非 messages 正文',mode==='json'?el('pre',JSON.stringify(config,null,2)):valueNode(config),'request-config'));nav('呼叫設定／輸出格式','request-config');}
  }
}
async function refreshTurn(){
  const listing=await fetch('/api/turns',{cache:'no-store'});if(!listing.ok)throw Error('turn list failed');
  const list=await listing.json();$('demo').textContent=list.demo?'合成資料預覽':'';
  const previous=$('turn').value;const previousText=$('turn').selectedOptions[0]?.textContent;
  const listKey=JSON.stringify(list.turns);
  if($('turn').dataset.list!==listKey){
    $('turn').replaceChildren();const latest=el('option','最新一輪');latest.value='latest';$('turn').append(latest);
    list.turns.forEach(t=>{const date=new Date(t.at).toLocaleString('zh-TW',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});const option=el('option',`${date} · ${t.preview||'（無文字）'} · ${t.status}`);option.value=String(t.id);$('turn').append(option);});
    if(previous!=='latest'&&!list.turns.some(t=>String(t.id)===previous)){const option=el('option',previousText||'先前選取的對話');option.value=previous;$('turn').append(option);}
    $('turn').value=previous||'latest';$('turn').dataset.list=listKey;
  }
  const id=$('turn').value==='latest'?list.turns[0]?.id:$('turn').value;
  if(!id){detail=null;loadedTurn=null;$('turn-info').textContent='目前還沒有已封存的對話呼叫';render();return;}
  const same=String(loadedTurn)===String(id);
  const response=await fetch('/api/turn?id='+encodeURIComponent(id),{cache:'no-store',headers:same&&turnTag?{'If-None-Match':turnTag}:{}});
  if(response.status===304)return;
  if(!response.ok)throw Error('turn failed');detail=await response.json();loadedTurn=id;turnTag=response.headers.get('ETag')||'';
  const oldAttempt=same?$('attempt').value:'';$('attempt').replaceChildren();
  detail.observers.forEach((call,i)=>{const option=el('option',`第 ${i+1} 次 · ${call.status}`);option.value=String(i);$('attempt').append(option);});
  const defaultIndex=Math.max(0,detail.observers.findLastIndex(c=>c.status==='completed'));
  $('attempt').value=oldAttempt!==''&&Number(oldAttempt)<detail.observers.length?oldAttempt:String(defaultIndex);
  $('attempt').disabled=!detail.observers.length;
  $('turn-info').textContent=`同一輪 · ${new Date(detail.run.at).toLocaleString('zh-TW')} · ${detail.run.status}`;
  if(currentSignature()!==activeDigest)render();
}
async function refresh(manual=false){
  if(busy)return;busy=true;
  try{if($('source').value==='turn'){await refreshTurn();}
    else {const response=await fetch('/api/snapshot',{cache:'no-store',headers:etag?{'If-None-Match':etag}:{}});
    if(response.status!==304){if(!response.ok)throw Error('read failed');const next=await response.json();etag=response.headers.get('ETag')||'';snapshot=next;$('demo').textContent=next.demo?'合成資料預覽':'';if(currentSignature()!==activeDigest)render();}}
    $('status').className='';$('status').textContent='已同步 '+new Date().toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',second:'2-digit'});if(manual)toast('已檢查最新封存');
  }catch{$('status').className='error';$('status').textContent='讀取失敗，稍後重試';if(!snapshot)$('content').replaceChildren(el('div','暫時無法讀取資料。請確認本機服務仍在執行。','empty'));}
  finally{busy=false;}
}
$('refresh').onclick=()=>refresh(true);$('source').onchange=()=>{controls();render();refresh();};
$('turn').onchange=()=>{turnTag='';refresh();};$('attempt').onchange=render;
document.querySelectorAll('[data-stage]').forEach(button=>button.onclick=()=>{stage=button.dataset.stage;document.querySelectorAll('[data-stage]').forEach(n=>n.classList.toggle('active',n===button));render();});
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{mode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(n=>n.classList.toggle('active',n===button));render();});
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(selected()?.raw||'');toast('已複製原始封存');}catch{toast('無法複製，請改用下載原始檔');}};
$('download').onclick=()=>{const raw=selected()?.raw;if(!raw)return;const url=URL.createObjectURL(new Blob([raw],{type:'text/plain;charset=utf-8'}));const a=el('a');a.href=url;a.download=selected().kind==='image'?'image-prompt.txt':$('source').value==='turn'?stage+'.json':'messages.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
refresh();setInterval(()=>{if(!document.hidden)refresh();},3000);
