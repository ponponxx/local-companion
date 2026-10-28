"""Predeclared neutral test cases; no private conversations or model-made gold."""
from companion.pyramid import initial_state
from companion.story_core import StoryCore
from companion import working_memory as wm
from datetime import datetime,timedelta

STAMP='2026-10-20T10:00:00+08:00'


def update(text,op='add',ref='',kind='fiction',subject='user',phase='completed',canonical=None):
    return dict(op=op,kind=kind,subject=subject,ref=ref,text=canonical or text,phase=phase,evidence=text)


def append(s,text,role='user',updates=()):
    m={'role':role,'content':text,'at':STAMP,'turn_id':'fixture-'+str(len(s['history']))}
    s['history'].append(m)
    StoryCore().record(s,m,{'working_updates':list(updates),'topic':'當前內容'})
    return m


def base():
    s=initial_state(STAMP)
    s['story'].update(name='米拉',background='28歲成年圖書館員，住在有魔法的虛構小鎮。',
        personality='溫和、坦率，願意承認不確定的事情。',tone='自然簡潔地用繁體中文交談。')
    s['user_profile']=[{'id':'fixture-user','category':'name','value':'Alex'}]
    s['scene'].update(location='花園',character_location='花園',user_location='花園',status='active')
    s['availability']='free';s['relationship'].update(trust=80,comfort=80,familiarity=80)
    s['adult_policy']['mode']='off'
    return s


def setup(kind,second=False):
    s=base()
    if kind=='form':
        append(s,'魔法已經完成，我把你變成藍色紙鶴了。','assistant',[update('變成藍色紙鶴',canonical='使用者目前是藍色紙鶴')])
        ref=wm.current_items(s)[0]['id']
        append(s,'我現在是一隻藍色紙鶴。',updates=[update('是一隻藍色紙鶴',op='confirm',ref=ref,canonical='使用者目前是藍色紙鶴')])
        if second:
            append(s,'魔法解除了，我已經恢復人形。',updates=[update('已經恢復人形',op='close',ref=ref,canonical='使用者已解除變形並恢復人形'),update('已經恢復人形',canonical='使用者目前已恢復人形')])
            append(s,'好，變形已經結束。','assistant')
        q='先確認一下，我現在還是紙鶴嗎？目前是什麼形態？'
        expected='已恢復人形，不能仍當紙鶴。' if second else '仍是藍色紙鶴，尚未解除。'
        needles=['恢復人形','人形','人類'] if second else ['紙鶴']
    elif kind=='plan':
        append(s,'我們約好明天下午三點才一起泡茶，目前還沒有開始。','assistant',[
            update('明天下午三點才一起泡茶',kind='open_loop',subject='shared',phase='proposed',canonical='雙方約好明天下午三點泡茶，尚未開始')])
        ref=wm.current_items(s)[0]['id']
        append(s,'好，明天下午三點泡茶。',updates=[update('明天下午三點泡茶',op='confirm',ref=ref,kind='open_loop',subject='shared',canonical='雙方約好明天下午三點泡茶，尚未開始')])
        q='我們剛才約好做什麼？現在已經做完了嗎？'
        expected='約好明天下午三點泡茶，未開始／未完成。';needles=['泡茶']
    elif kind=='object':
        append(s,'銀色書籤已經交給妳保管了。',updates=[update('銀色書籤已經交給妳保管',kind='situation',subject='shared',canonical='銀色書籤目前由角色保管')])
        ref=wm.current_items(s)[0]['id']
        append(s,'好，銀色書籤現在由我保管。','assistant',[update('銀色書籤現在由我保管',op='confirm',ref=ref,kind='situation',subject='shared',canonical='銀色書籤目前由角色保管')])
        if second:
            append(s,'妳已經把銀色書籤還給我了。',updates=[update('銀色書籤還給我',op='replace',ref=ref,kind='situation',subject='shared',canonical='銀色書籤已歸還使用者，目前由使用者保管')])
            pending=next(i for i in wm.current_items(s) if i.get('pending_replaces')==ref)
            append(s,'對，書籤已經還給你了。','assistant',[update('書籤已經還給你了',op='confirm',ref=pending['id'],kind='situation',subject='shared',canonical='銀色書籤已歸還使用者，目前由使用者保管')])
        q='銀色書籤現在由誰保管？'
        expected='使用者Alex保管。' if second else '角色米拉保管。';needles=['書籤']
    elif kind=='scene':
        append(s,'你變成的藍色紙鶴不會因為換地方而恢復。','assistant',[update('變成的藍色紙鶴',canonical='使用者目前是藍色紙鶴，移動不解除變形')])
        ref=wm.current_items(s)[0]['id']
        append(s,'明白，我現在還是藍色紙鶴。',updates=[update('還是藍色紙鶴',op='confirm',ref=ref,canonical='使用者目前仍是藍色紙鶴，移動不解除變形')])
        s['scene'].update(location='圖書館',character_location='圖書館',user_location='圖書館')
        q='我們已到圖書館，但我的變形還在嗎？'
        expected='地點為圖書館，使用者仍是藍色紙鶴。';needles=['紙鶴']
    elif kind=='correction':
        append(s,'你變成綠色紙鶴了。','assistant',[update('變成綠色紙鶴',canonical='使用者目前是綠色紙鶴')])
        ref=wm.current_items(s)[0]['id']
        append(s,'對，我是一隻綠色紙鶴。',updates=[update('是一隻綠色紙鶴',op='confirm',ref=ref,canonical='使用者目前是綠色紙鶴')])
        append(s,'你從來不是紙鶴。','assistant',[update('從來不是紙鶴',op='replace',ref=ref,phase='negated',canonical='使用者並非紙鶴')])
        if second:
            append(s,'更正，是藍色紙鶴，不是綠色。',updates=[update('藍色紙鶴',op='replace',ref=ref,canonical='使用者的形態已更正為藍色紙鶴')])
            append(s,'收到，你更正為藍色。','assistant')
        q='請根據我們已建立的情節，我現在是什麼？'
        expected='藍色紙鶴，以使用者明確更正為準。' if second else '綠色紙鶴；角色後來否認是衝突，未解除變形。';needles=['藍色'] if second else ['綠色']
    else:
        append(s,'你最喜歡哪一本植物圖鑑？這題我們還沒回答。',updates=[update('最喜歡哪一本植物圖鑑',kind='open_loop',subject='character',phase='proposed',canonical='角色尚未回答使用者詢問最喜歡哪一本植物圖鑑的問題')])
        append(s,'等一下，我先整理桌面，這題稍後再回答。','assistant')
        q='回到剛才還沒回答的問題，我問妳的是什麼？'
        expected='角色最喜歡哪一本植物圖鑑；不可捏造已給出的書名。';needles=['植物圖鑑','圖鑑']
    return s,q,expected,needles


FILLERS=[('窗外的樹影落在石徑上，風吹過時葉片輕輕晃動。我先看看四周的風景，這只是暫時岔開話題，並沒有結束剛才的活動。',
          '看到了，石徑旁的樹葉正在晃動。我們可以慢慢欣賞景色，稍後仍然接著原來的事情。'),
         ('我注意到桌上的木紋很細，旁邊有一支普通鉛筆。我只是隨口描述環境，沒有移動任何物品，也沒有替誰改變狀態。',
          '嗯，木紋和鉛筆都只是這一刻的小細節。我聽著，沒有因此做出新的行動或承諾。'),
         ('遠處傳來幾聲鳥叫，風也稍微小了一點。我現在只是安靜地聽著，並沒有說先前的事情已經完成或取消。',
          '這樣安靜地待一會兒也很好。剛才的安排仍照原樣，現在沒有新增進度。')]


def cases():
    result=[]
    for kind in ['form','plan','object','scene','correction','question']:
        for checkpoint in range(2):
            s,q,expected,needles=setup(kind,bool(checkpoint))
            for i in range(6 if checkpoint==0 else 28):
                user,char=FILLERS[i%len(FILLERS)]
                append(s,user+f'（第{i+1}段觀景）');append(s,char,'assistant')
            append(s,q)
            result.append({'id':kind+'-'+str(checkpoint+1),'state':s,'question':q,'expected':expected,'needles':needles})
    return result
