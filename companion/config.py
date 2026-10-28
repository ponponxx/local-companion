from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Shared by model launcher, all preflights and Observer reserve fallback.
CONTEXT_CAPACITY = 8192
CONTEXT_SAFETY = 64


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Config:
    token: str
    allowed_user: int
    data_dir: Path
    llm_url: str
    llm_model: str
    max_tokens: int
    llm_timeout: float
    whisper_bin: Path
    whisper_model: Path
    ffmpeg: Path
    tts_voice: str
    max_voice_seconds: int
    image_python: Path
    image_model: str
    image_model_path: str
    image_quantize: int
    image_width: int
    image_height: int
    image_steps: int
    image_timeout: float
    timezone: str
    router_mode: str = 'off'
    router_python: Path = ROOT / '.venv-router/bin/python'
    router_model_path: Path = ROOT / '.local/models/embeddinggemma-2'
    persona_policy_backend: str = 'rules'
    persona_policy_url: str = 'http://127.0.0.1:8081/v1'
    persona_policy_model: str = 'persona-policy'
    gpt_review_bin: Path = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    gpt_review_model: str = 'gpt-5.6-terra'
    chat_core: str = 'legacy'

    @classmethod
    def load(cls, require_credentials: bool = True) -> Config:
        load_dotenv(ROOT / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        try:
            user = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0") or "0")
        except ValueError:
            raise ValueError("TELEGRAM_ALLOWED_USER_ID 必須是數字") from None
        if require_credentials and (not token or user <= 0):
            raise ValueError("請在 .env 設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_ALLOWED_USER_ID")
        url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
            raise ValueError("LLM_BASE_URL 必須是本機 HTTP 位址")
        path = lambda key, default: local_path(os.getenv(key, default))
        config = cls(
            token, user, path("DATA_DIR", ".data/telegram"), url,
            os.getenv("LLM_MODEL", "local-chat"), int(os.getenv("LLM_MAX_TOKENS", "384")),
            float(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
            path("WHISPER_BIN", ".local/bin/whisper-cli"), path("WHISPER_MODEL", ".local/models/ggml-small.bin"),
            path("FFMPEG_BIN", ".local/bin/ffmpeg"), os.getenv("TTS_VOICE", "Meijia"),
            int(os.getenv("MAX_VOICE_SECONDS", "120")), path("IMAGE_PYTHON", ".venv-image/bin/python"),
            os.getenv("IMAGE_MODEL", "flux2-klein-4b"), os.getenv("IMAGE_MODEL_PATH", "mflux-community/flux2-klein-4b-mflux-q4" if os.getenv("IMAGE_MODEL", "flux2-klein-4b") == "flux2-klein-4b" else ""),
            int(os.getenv("IMAGE_QUANTIZE", "4")), int(os.getenv("IMAGE_WIDTH", "512")),
            int(os.getenv("IMAGE_HEIGHT", "512")), int(os.getenv("IMAGE_STEPS", "4")),
            float(os.getenv("IMAGE_TIMEOUT_SECONDS", "900")),
            os.getenv("TIMEZONE", "Asia/Taipei").strip(),
            os.getenv('ROUTER_MODE', 'off').strip(),
            path('ROUTER_PYTHON', '.venv-router/bin/python'),
            path('ROUTER_MODEL_PATH', '.local/models/embeddinggemma-2'),
            os.getenv('PERSONA_POLICY_BACKEND', 'rules').strip(),
            os.getenv('PERSONA_POLICY_URL', 'http://127.0.0.1:8081/v1').rstrip('/'),
            os.getenv('PERSONA_POLICY_MODEL', 'persona-policy').strip(),
            path('GPT_REVIEW_BIN', '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'),
            os.getenv('GPT_REVIEW_MODEL', 'gpt-5.6-terra').strip(),
            os.getenv('CHAT_CORE', 'pyramid').strip(),
        )
        if config.chat_core not in {'legacy', 'pyramid'}:
            raise ValueError('CHAT_CORE 須為 legacy 或 pyramid')
        if config.persona_policy_backend not in {'rules', 'local_model'}:
            raise ValueError('PERSONA_POLICY_BACKEND 須為 rules 或 local_model')
        policy_url = urlparse(config.persona_policy_url)
        if policy_url.scheme != 'http' or policy_url.hostname not in {'127.0.0.1', 'localhost', '::1'} or policy_url.username or policy_url.password:
            raise ValueError('PERSONA_POLICY_URL 必須是本機 HTTP 位址')
        if config.router_mode not in {'off', 'shadow', 'active'}:
            raise ValueError('ROUTER_MODE 須為 off、shadow 或 active')
        if not (1 <= config.max_tokens <= 1024 and 1 <= config.max_voice_seconds <= 120):
            raise ValueError("回覆 token 上限須為 1–1024，語音上限須為 1–120 秒")
        if config.image_quantize not in {3, 4, 5, 6, 8}:
            raise ValueError("IMAGE_QUANTIZE 須為 3、4、5、6 或 8")
        if config.image_model not in {"z-image-turbo", "flux2-klein-4b", "flux2-klein-9b", "flux2-klein-base-4b", "flux2-klein-base-9b"}:
            raise ValueError("目前圖片 adapter 支援 Z-Image Turbo 與 FLUX.2 Klein 系列")
        if any(size < 256 or size > 1024 or size % 16 for size in (config.image_width, config.image_height)):
            raise ValueError("圖片尺寸須為 256–1024 且為 16 的倍數")
        if not 1 <= config.image_steps <= 50 or min(config.llm_timeout, config.image_timeout) <= 0:
            raise ValueError("生成步數或逾時設定無效")
        try:
            ZoneInfo(config.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("TIMEZONE 必須是有效的 IANA 時區，例如 Asia/Taipei") from None
        return config
