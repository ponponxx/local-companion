<p align="center"><img src="docs/assets/bot-avatar.jpg" alt="Local Companion project avatar" width="240"><br><sub>Malfoid｜性轉馬份</sub></p>

# Local Companion

**A local AI character engine with a daily life — and an inner loop you can inspect.**

[繁體中文](README.zh-TW.md) · [Quick start](#quick-start-macos--apple-silicon) · [Architecture](docs/ARCHITECTURE.md) · [Limitations](docs/LIMITATIONS.md)

Experimental **v0.1.0-alpha.1** · Python 3.12 · macOS / Apple Silicon first · Apache-2.0

- **Context-aware character photos:** current outfit, scene, time and camera assembled into an inspectable image prompt.
- **A persistent life:** scheduled activities, scene changes, current outfits and small fictional events.
- **Decisions before dialogue:** explicit needs, relationship and emotion state select actions; a Writer phrases the result.
- **Inspectable turns:** view the Observer input, its raw output and the same turn's Writer Prompt in a local browser.

Telegram is the conversation interface. A Mini App edits settings; a separate
local workbench lets you explore decision rules. Memory, state and diagnostics
stay on your computer. Telegram still transports messages; optional news,
search, model downloads and explicitly requested external reviews use the network.

## Character photos that start from the current scene

Use `/photo` to combine the character's appearance and current outfit with your
shot request, registered background, time-aware lighting and camera direction.
A bounded local visual planner proposes the shot; Python owns the final Prompt
assembly. MFLUX then renders it locally with the selected image weights.

![Context-aware character photo pipeline](docs/diagrams/image-pipeline.svg)

[How the image pipeline works](docs/IMAGES.md) · [生圖流程中文圖解](docs/IMAGES.zh-TW.md)

`/photo`, `/selfie` and `/image` have different paths. This is prompt-based
continuity, not a face-lock guarantee; the guide explains what is model-planned,
what is program-owned, and how to inspect the exact prompt.

## Models

Chat: Qwen3.5-9B GGUF (Q4_K_M). Optional images: Z-Image Turbo via MFLUX.
[Model sources, tested configurations, downloads and licenses](docs/MODELS.md)
include the exact development 4-bit image distribution and distinguish it from
the public defaults. No weights are bundled.

## Inside the engine

![Six cores and their handoffs](docs/diagrams/cores.svg)

**Observer proposes → rules decide → delivery sets tone → the Editor assembles → Writer speaks.**
StoryCore supplies sourced context throughout; the state reducer owns confirmed facts.
These are six responsibilities, not six models.

[Four architecture diagrams and source-code mapping](docs/ARCHITECTURE.md) · [六個核心的中文說明](docs/CORES.zh-TW.md)

## Try the inspector without a model or Telegram

```sh
python3 scripts/prompt_viewer.py --demo --port 8768
```

Open **http://127.0.0.1:8768/**. All demonstration data is synthetic. This shows
the inspection UI; it does not run inference or prove model quality.

## Quick start (macOS / Apple Silicon)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then in this checkout:

```sh
uv sync --python 3.12 --extra setup
cp .env.example .env
uv run scripts/init_demo.py
uv run --extra setup scripts/setup_local.py --chat
uv run scripts/configure_telegram.py
```

The setup step downloads a compatible llama.cpp build and a GGUF model — several
GB, not included in the repository. Suggested default: Qwen3.5-9B Q4_K_M. The
previous private fine-tune is not required or redistributed. Its measured behavior
must not be assumed for this public model.

Run these in separate terminals:

```sh
uv run scripts/start_chat.py
```

```sh
uv run -m companion
```

Message your bot. `/settings`, `/outfit`, `/language CH`, `/profile`, `/life`,
`/debug last` and `/prompt` expose the main controls. The demo disables proactive
messages; enable them in `/settings` when ready. Ordinary text waits for a
**10-second quiet window** so multiple messages can form one turn.

Inspect your own local recordings:

```sh
uv run scripts/prompt_viewer.py --database .data/telegram/memory.sqlite3
```

Explore numeric decisions separately:

```sh
uv run -m companion.workbench --port 8765
```

## What is ready, and what is experimental?

| Area | Current scope |
|---|---|
| Telegram | Single allowlisted user, settings buttons and Mini App integration |
| State | Per-person travel, scene outfits, time and scene-tagged memory |
| Decision core | Inspectable engineering rules, not a validated psychology model |
| Memory | Persistent originals plus selected context; recall can still be wrong |
| Media | Optional voice and on-demand images; separate setup required |
| Platform | Local Apple Silicon deployment exercised; Linux/Windows not validated |

The previous M4 16GB setup ran a 9B Q4 model at 8K context. Expect variable
latency, shared-memory pressure with images, and semantic mistakes. We do not
claim a guaranteed 30-second response, flawless memory or human-level emotion.
The alpha needs contributors working on correctness and installation portability.

## Development

```sh
uv run python -m unittest discover -s test -p 'test_*.py'
uv run python scripts/check_public.py
```

Tests use synthetic inputs and fake model/transport providers. They do not
certify real model semantics. See [contributing](CONTRIBUTING.md),
[privacy/security](SECURITY.md), [optional features](docs/SETUP.md) and
[release notes](CHANGELOG.md) and [local validation](docs/VALIDATION.md). Model weights have separate licenses.
