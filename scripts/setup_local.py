"""Provision project-local runtimes. Run with: uv run --extra setup scripts/setup_local.py --all"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from companion.config import Config

LOCAL = ROOT / ".local"
BIN = LOCAL / "bin"
DOWNLOADS = LOCAL / "downloads"
LLAMA_TAG = "b11569"
WHISPER_TAG = "v1.9.5"
MFLUX_VERSION = "0.22.0"


def request_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "local-companion-setup"}), timeout=60) as response:
        return json.load(response)


def download(url: str, target: Path, digest: str | None = None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("rb") as handle:
            if not digest or hashlib.file_digest(handle, "sha256").hexdigest() == digest:
                return
    temporary = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {target.name}", flush=True)
    offset = temporary.stat().st_size if temporary.exists() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    with urllib.request.urlopen(request, timeout=120) as source:
        mode = "ab" if offset and source.status == 206 else "wb"
        with temporary.open(mode) as destination:
            shutil.copyfileobj(source, destination)
    if digest:
        with temporary.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != digest:
            temporary.unlink()
            raise RuntimeError("下載檔案 SHA256 不符")
    temporary.replace(target)


def run(args):
    environment = os.environ.copy()
    environment.setdefault("UV_HTTP_TIMEOUT", "300")
    environment.setdefault("UV_CONCURRENT_DOWNLOADS", "4")
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, env=environment)


def select_chat_file(filenames, quant):
    suffixes = (f".{quant}.gguf", f"-{quant}.gguf")
    matches = [name for name in filenames if name.endswith(suffixes) and "mmproj" not in name.lower()]
    if len(matches) != 1:
        raise RuntimeError("找不到唯一的單檔量化模型；請確認 CHAT_MODEL_REPO 與 CHAT_MODEL_QUANT")
    return matches[0]


def llama():
    directory = LOCAL / "runtime" / f"llama-{LLAMA_TAG}"
    if not (BIN / "llama-server").exists():
        release = request_json(f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{LLAMA_TAG}")
        asset = next(asset for asset in release["assets"] if asset["name"].endswith("bin-macos-arm64.tar.gz"))
        archive = DOWNLOADS / asset["name"]
        digest = (asset.get("digest") or "").removeprefix("sha256:") or None
        download(asset["browser_download_url"], archive, digest)
        directory.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as bundle:
            bundle.extractall(directory, filter="data")
        executable = next(directory.rglob("llama-server"))
        (BIN / "llama-server").symlink_to(executable)
    config = Config.load(require_credentials=False)
    repo = os.getenv("CHAT_MODEL_REPO", "unsloth/Qwen3.5-9B-GGUF")
    quant = os.getenv("CHAT_MODEL_QUANT", "Q4_K_M")
    from huggingface_hub import HfApi, hf_hub_download
    info = HfApi().model_info(repo, files_metadata=True)
    filename = select_chat_file([file.rfilename for file in info.siblings], quant)
    print(f"Downloading chat model {quant}", flush=True)
    path = hf_hub_download(repo, filename, revision=info.sha)
    (LOCAL / "models").mkdir(exist_ok=True)
    (LOCAL / "models" / "chat-model.json").write_text(json.dumps({"repo": repo, "revision": info.sha, "path": path}, indent=2))


def voice():
    import imageio_ffmpeg
    ffmpeg = BIN / "ffmpeg"
    # uv run without setup extras can remove imageio-ffmpeg from .venv, so
    # retain the standalone executable rather than linking into site-packages.
    if ffmpeg.is_symlink():
        ffmpeg.unlink()
    if not ffmpeg.exists():
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), ffmpeg)
    if not (BIN / "whisper-cli").exists():
        archive = DOWNLOADS / f"whisper-{WHISPER_TAG}.tar.gz"
        download(f"https://github.com/ggml-org/whisper.cpp/archive/refs/tags/{WHISPER_TAG}.tar.gz", archive)
        source = LOCAL / "runtime" / f"whisper.cpp-{WHISPER_TAG[1:]}"
        with tarfile.open(archive) as bundle:
            bundle.extractall(source.parent, filter="data")
        cmake = ROOT / ".venv/bin/cmake"
        run([cmake, "-S", source, "-B", source / "build", "-DCMAKE_BUILD_TYPE=Release", "-DGGML_METAL=ON"])
        run([cmake, "--build", source / "build", "--config", "Release", "-j", "4", "--target", "whisper-cli"])
        (BIN / "whisper-cli").symlink_to(source / "build/bin/whisper-cli")
    from huggingface_hub import hf_hub_download
    source = Path(hf_hub_download("ggerganov/whisper.cpp", "ggml-small.bin"))
    target = LOCAL / "models/ggml-small.bin"
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        target.symlink_to(source)


def images():
    uv = shutil.which("uv") or str(BIN / "uv")
    if not (ROOT / ".venv-image/bin/python").exists():
        run([uv, "venv", "--python", "3.12", ROOT / ".venv-image"])
    run([uv, "pip", "install", "--python", ROOT / ".venv-image/bin/python", f"mflux=={MFLUX_VERSION}"])
    # Models download on the first explicit image request. --smoke-image warms the
    # model cache using a neutral prompt before real Telegram use.


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--images", action="store_true")
    args = parser.parse_args()
    Config.load(require_credentials=False)
    os.environ.setdefault("HF_HOME", str(LOCAL / "huggingface"))
    for path in (BIN, DOWNLOADS):
        path.mkdir(parents=True, exist_ok=True)
    if args.all or args.chat:
        llama()
    if args.all or args.voice:
        voice()
    if args.all or args.images:
        images()
    if not any(vars(args).values()):
        parser.print_help()


if __name__ == "__main__":
    main()
