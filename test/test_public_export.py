import hashlib,json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class PublicExportTests(unittest.TestCase):
 def test_demo_initialization_is_original_and_cannot_overwrite_existing_state(self):
  with tempfile.TemporaryDirectory() as directory:
   command=[sys.executable,str(ROOT/'scripts/init_demo.py'),'--data-dir',directory]
   first=subprocess.run(command,capture_output=True,text=True,cwd=ROOT)
   self.assertEqual(first.returncode,0,first.stderr)
   path=Path(directory)/'memory.sqlite3'
   with sqlite3.connect(path) as db:
    saved=dict(db.execute('select key,value from settings'))
    self.assertEqual(json.loads(saved['persona'])['name'],'林青')
    self.assertEqual(saved['proactive_enabled'],'false')
    self.assertEqual(saved['scene_background_world'],'original_world')
    self.assertEqual(db.execute('select count(*) from messages').fetchone()[0],0)
    self.assertEqual(db.execute('select count(*) from user_profile_facts').fetchone()[0],0)
   before=hashlib.sha256(path.read_bytes()).hexdigest()
   second=subprocess.run(command,capture_output=True,text=True,cwd=ROOT)
   self.assertNotEqual(second.returncode,0)
   self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
 def test_example_configuration_uses_explicit_current_core_and_blank_credentials(self):
  text=(ROOT/'.env.example').read_text().splitlines()
  self.assertIn('CHAT_CORE=pyramid',text)
  self.assertIn('TELEGRAM_BOT_TOKEN=',text)
  self.assertIn('TELEGRAM_ALLOWED_USER_ID=',text)
  self.assertEqual(json.loads((ROOT/'characters/lin-qing.json').read_text())['story']['personality'],'')

 def test_model_filename_styles_and_ambiguous_selection(self):
  from scripts.setup_local import select_chat_file
  for name in ['Example.Q4_K_M.gguf','Example-Q4_K_M.gguf']:
   self.assertEqual(select_chat_file([name,'mmproj-Q4_K_M.gguf','Other-Q8_0.gguf'],'Q4_K_M'),name)
  with self.assertRaises(RuntimeError):select_chat_file(['Example.Q4_K_M.gguf','Example-Q4_K_M.gguf'],'Q4_K_M')
  with self.assertRaises(RuntimeError):select_chat_file(['Example-Q8_0.gguf'],'Q4_K_M')
