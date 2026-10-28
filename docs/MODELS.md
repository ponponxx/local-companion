# Models and runtimes / 模型與推論工具

[Project](../README.md) · [繁體中文首頁](../README.zh-TW.md) · [Setup](SETUP.md)

**Model names and sources are documented here; weights are not uploaded to this repository.**
模型名稱、來源與配置公開，權重由使用者另行下載。Public defaults and the development
configuration are different; do not transfer measured behavior between checkpoints.

## Inventory

| Role / 用途 | Model | Required? | Public setup versus development |
|---|---|---|---|
| Observer, Writer and supported background extraction | [Qwen3.5-9B GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) | Yes for local chat | Public suggested default, Q4_K_M. Its conversational quality has not been newly benchmarked by this export. |
| Development chat checkpoint | [qwen3.5-9b-nsfw-captioning-v5 GGUF](https://huggingface.co/mradermacher/qwen3.5-9b-nsfw-captioning-v5-GGUF) | No; alternative | The checkpoint used by the development deployment, Q4_K_M. Not the public download default. |
| Image generation | [Z-Image Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | Only for images | Public `.env.example` selects this family, 4-bit, 512×512, 9 steps, with `IMAGE_MODEL_PATH` empty. |
| Development image weights | [Z-Image-Turbo-mflux-4bit](https://huggingface.co/filipstrand/Z-Image-Turbo-mflux-4bit) | Optional prequantized distribution | Actual development image path contains this MFLUX-compatible 4-bit distribution. Exact reproduction recipe below. |
| Optional semantic retrieval | [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2) | No for basic chat | Development uses a separate CPU embedding environment. Public quick start does not install it; retrieval retains keyword fallback. It does not write character replies. |
| Speech recognition | [Whisper small, ggml-small.bin](https://huggingface.co/ggerganov/whisper.cpp) | Only for voice input | Multilingual small model via whisper.cpp; separate optional setup. |
| Speech output | macOS system speech, configured voice `Meijia` | Only for voice output | Uses `/usr/bin/say`; no custom TTS checkpoint or voice cloning weights are bundled. |

The same chat model can serve multiple core roles; the six cores do not require
six model downloads. **MFLUX and llama.cpp are runtimes, not replacement model names.**
The image runtime loads its image model's own text encoder and VAE; it does not
reuse the companion's chat 9B as the image text encoder.

## Public versus measured chat configuration

The public setup selects `unsloth/Qwen3.5-9B-GGUF` / `Q4_K_M`. At the 2026-10-28
catalog check its revision was `3885219b6810b007914f3a7950a8d1b469d598a5`.
The installer resolves the repository revision at download time and records it in
the local `.local/models/chat-model.json`; the example revision here is not a
promise that a later download will use the same files.

The development checkpoint is pinned in its existing manifest to:

- Repository: `mradermacher/qwen3.5-9b-nsfw-captioning-v5-GGUF`
- Revision: `f7ee28de74f93f24ad54459491e4e328de49deee`
- File: `qwen3.5-9b-nsfw-captioning-v5.Q4_K_M.gguf`
- Context: 8192 tokens, one inference slot, thinking disabled.
- Platform used for local trials: Apple M4, 16GB unified memory.

Changing `.env` to that alternative repository before running `setup_local.py --chat`
requests its current repository version; it does not automatically pin the
historical revision above. Existing speed/quality results concern their stated
checkpoints and prompts, not every 9B model. Model naming does not guarantee any
particular content behavior or eliminate identity/memory mistakes.

## Image model: reproduce the development distribution

Actual development settings, verified from the configured path and cached model metadata:

```dotenv
IMAGE_MODEL=z-image-turbo
IMAGE_MODEL_PATH=.local/models/z-image-turbo-q4
IMAGE_QUANTIZE=4
IMAGE_WIDTH=512
IMAGE_HEIGHT=512
IMAGE_STEPS=9
IMAGE_TIMEOUT_SECONDS=900
```

- Runtime: **MFLUX 0.22.0** in a separate image environment.
- Distribution: `filipstrand/Z-Image-Turbo-mflux-4bit`.
- Cached revision: `b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b`.
- Upstream family: `Tongyi-MAI/Z-Image-Turbo` (6B image model).
- Current worker chooses a fresh random seed. Dimensions and steps alone do not reproduce pixels.

From the checkout, after reviewing the distribution's model card:

```sh
uv run --extra setup scripts/setup_local.py --images
uv run --extra setup python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='filipstrand/Z-Image-Turbo-mflux-4bit', revision='b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b', local_dir='.local/models/z-image-turbo-q4')"
```

Then put the settings above into your local `.env` and restart **your** bot.
Do not copy a developer's absolute filesystem path.

**Public default difference:** `IMAGE_MODEL_PATH=` is empty in the shipped example.
That delegates upstream weight resolution to MFLUX and is not the same as pointing
at the downloaded prequantized directory. Prequantized weights can avoid the
transient cost of loading originals before quantization. Disk size is not peak
RAM, and concurrent image/chat use competes for unified memory. No fixed render
time or output quality is promised.

The optional setup/worker already exists; this documentation update did not
redownload weights, generate an image or change the live bot's configuration.

## Voice and optional embeddings

Voice installation is `uv run --extra setup scripts/setup_local.py --voice`.
It downloads `ggml-small.bin` and builds whisper.cpp. Speech synthesis uses the
configured macOS voice; the image model and chat model are not TTS engines.

EmbeddingGemma 2 is optional and **not** the older `embeddinggemma-300m` model.
The embedding worker expects an already-installed local checkpoint and compatible
Torch / Transformers / SentenceTransformers packages in `.venv-router`, uses CPU
text embeddings with offline-only loading, and limits its input sequence length.
The public quick start does not provision that advanced environment. Leaving it
absent does not silently download it during foreground chat.

## Versions and licenses

Setup pins llama.cpp `b11569`, MFLUX `0.22.0`, and whisper.cpp `v1.9.5`.
These are installer/runtime pins, separate from model revisions. Model-card license
metadata was checked on **2026-10-28**:

| Distribution | Declared model-card metadata |
|---|---|
| Qwen3.5-9B public GGUF | Apache-2.0 |
| Captioning-v5 GGUF alternative | No license field in that GGUF card; consult its linked base-model card and original terms |
| Upstream Z-Image Turbo | Apache-2.0 |
| MFLUX 4-bit Z-Image distribution | `other` / `tongyi-qianwen-license`; differs from the current upstream metadata |
| EmbeddingGemma 2 | Apache-2.0 |
| whisper.cpp GGML distribution | MIT |

The application repository's Apache-2.0 license does not relicense downloaded
models or resolve differences between distributors' license metadata. Use the
linked model cards and their actual terms; this table reports metadata, not a
legal compatibility determination. No third-party weights are redistributed here.

A machine-readable, non-private inventory is in [models.json](models.json).
未知的本地版本明確留空，不會拿網路最新版本冒充已安裝版本。
