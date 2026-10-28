"""Initialize only a NEW local database. No model call or Telegram request."""
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from companion.store import Store
from companion.config import Config

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-dir',type=Path,default=None);args=parser.parse_args()
 path=(args.data_dir or Config.load(require_credentials=False).data_dir)/'memory.sqlite3'
 if path.exists():parser.error('Database already exists; use /settings to edit it. Nothing was changed.')
 os.umask(0o077);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 demo=json.loads((ROOT/'characters/lin-qing.json').read_text());store=Store(path)
 try:
  store.set('persona',json.dumps(demo['story'],ensure_ascii=False));store.set('appearance',json.dumps(demo['appearance'],ensure_ascii=False));store.set('traits',json.dumps(demo['traits']))
  store.set('scene_background_world','original_world');store.set('proactive_enabled','false')
 finally:store.close()
 print('Created fictional Lin Qing profile. User profile is empty. Proactive messages are off; enable explicitly in /settings.')
if __name__=='__main__':main()
