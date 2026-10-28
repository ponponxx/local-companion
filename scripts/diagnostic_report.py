"""Read-only local archive listing/report. No model or Telegram calls."""
import argparse
from pathlib import Path
from companion.diagnostics import Diagnostics

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('run_id',nargs='?',type=int)
 parser.add_argument('--db',type=Path,default=Path.home()/'Library/Application Support/LocalCompanion/.data/telegram/diagnostics.sqlite3')
 parser.add_argument('--kind');parser.add_argument('--limit',type=int,default=12)
 args=parser.parse_args();archive=Diagnostics.readonly(args.db)
 try:
  if args.run_id is not None:print(archive.report(args.run_id))
  else:
   query='SELECT id,kind,started_at,status FROM runs';params=[]
   if args.kind:query+=' WHERE kind=?';params.append(args.kind)
   query+=' ORDER BY id DESC LIMIT ?';params.append(args.limit)
   for r in archive.db.execute(query,params):print(f"#{r['id']} | {r['started_at']} | {r['kind']} | {r['status']}")
 finally:archive.close()
if __name__=='__main__':main()
