"""Conservative release-file checks; not a proof that all secrets are absent."""
from pathlib import Path
import json,re,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={'.git','.venv','.local','.data','__pycache__','outputs','backups','experiments'}
def candidates():
 git=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True)
 if git.returncode==0 and git.stdout:return [ROOT/p.decode() for p in git.stdout.split(b'\0') if p]
 return [p for p in ROOT.rglob('*') if p.is_file() and not any(x in EXCLUDE or x.startswith('.venv') for x in p.relative_to(ROOT).parts)]
def inspect(paths):
 issues=[]
 patterns=[('telegram_token',re.compile(r'\b\d{7,12}:[A-Za-z0-9_-]{30,}\b')),('github_token',re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b')),('private_key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),('developer_home',re.compile(r'/Users/[A-Za-z0-9_.-]+/'))]
 for p in paths:
  rel=p.relative_to(ROOT);parts=rel.parts
  if p.is_symlink():issues.append({'path':str(rel),'kind':'symlink'});continue
  if any(x in EXCLUDE for x in parts) or (p.name.startswith('.env') and p.name!='.env.example') or p.suffix in {'.db','.sqlite3','.sqlite','.gguf','.safetensors','.pem','.key','.log'}:
   issues.append({'path':str(rel),'kind':'private_or_generated_file'})
  if p.stat().st_size>5_000_000:issues.append({'path':str(rel),'kind':'large_file'})
  try:text=p.read_text()
  except UnicodeDecodeError:
   if p.suffix not in {'.png','.jpg','.jpeg','.webp','.gif'}:issues.append({'path':str(rel),'kind':'unexpected_binary'})
   continue
  # Scanner patterns describe forbidden terms, not user data.
  if rel==Path('scripts/check_public.py'):continue
  for name,regex in patterns:
   if regex.search(text):issues.append({'path':str(rel),'kind':name})
 return issues
if __name__=='__main__':
 files=candidates();issues=inspect(files);print(json.dumps({'files_checked':len(files),'passed':not issues,'issues':issues,'scope':'tracked files if staged, otherwise allowlisted source tree; heuristic only'},ensure_ascii=False,indent=2));sys.exit(bool(issues))
