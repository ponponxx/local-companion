"""Frozen local-time lighting, separate from permanent place geometry."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

# Old generated place descriptions sometimes bake in a time of day. Reject the
# whole description for rendering rather than editing pieces of generated prose.
TEMPORAL = re.compile(r'陽光|日光|晨光|夕陽|夕照|落日|日落|夜幕|星空|月光|白天|夜晚|夜色|白晝|清晨|黃昏|sunlight|daylight|sunset|sunrise|moonlight|starlit|night sky|at night',re.I)


def snapshot_lighting(at, background):
    if isinstance(at,str):at=datetime.fromisoformat(at)
    local=at.astimezone(ZoneInfo('Asia/Taipei'));hour=local.hour
    period='清晨' if 5<=hour<7 else '白天' if 7<=hour<17 else '黃昏' if 17<=hour<19 else '夜晚'
    env=background.get('environment','unknown')
    if env=='indoor':
        light={'清晨':'室內柔和照明，窗外微亮','白天':'室內白天光線','黃昏':'室內柔和照明，窗外漸暗','夜晚':'室內柔和照明，窗外黑暗，沒有日光'}[period]
    else:
        light={'清晨':'天色微亮，低強度自然光','白天':'白天自然光','黃昏':'天色漸暗，柔和暮光','夜晚':'深暗夜空與低照度環境，沒有日光'}[period]
    return {'at':local.isoformat(timespec='minutes'),'period':period,'description':f'{local:%Y年%m月%d日 %H:%M}，{period}；{light}'}


def timeless_background(background):
    result=dict(background)
    if TEMPORAL.search(result.get('background','')):
        result['background']=result.get('location') or '地點未確認'
        result['temporal_fallback']=True
    return result
