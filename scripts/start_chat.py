"""Start the downloaded GGUF server, bound only to loopback."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from companion.config import Config, CONTEXT_CAPACITY


def main():
    config = Config.load(require_credentials=False)
    address = urlparse(config.llm_url)
    manifest = ROOT / ".local/models/chat-model.json"
    executable = ROOT / ".local/bin/llama-server"
    if not manifest.exists() or not executable.exists():
        sys.exit("請先執行 scripts/setup_local.py --chat")
    path = json.loads(manifest.read_text())["path"]
    if not Path(path).is_file():
        sys.exit("聊天模型權重不存在，請重新執行安裝步驟")
    args = [str(executable), "--model", path, "--alias", os.getenv("LLM_MODEL", "local-chat"),
            "--host", address.hostname, "--port", str(address.port or 8080), "--ctx-size", str(CONTEXT_CAPACITY), "--parallel", "1", "--n-gpu-layers", "99",
            "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}']
    os.execv(str(executable), args)


if __name__ == "__main__":
    main()
