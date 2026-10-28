# Known limits

This alpha exposes a research workflow, not a finished virtual person.

- A schema-valid interpretation may still name the wrong actor, place or stage.
- The Writer may invent details, repeat phrasing, confuse identities or ignore brevity.
- Current state is authoritative; older memories can still bias generation incorrectly.
- Stored originals are not all included in each request. Context is 8192 tokens with
  per-stage reserves and selected memory. Oversized necessary input can still stop a turn.
- Character emotion/relationship rules are authored, not clinically validated.
- The default public model differs from the developer's previous fine-tune. No live
  generation quality or speed claim is made for this export's suggested model.
- M4 16GB is a constrained shared-memory machine; image generation competes with chat.
- Single-user Telegram access only; this is not a multi-tenant hosted service.
- Mini App requires an operator-supplied HTTPS endpoint and Telegram authentication.
- Local model inference is not an offline guarantee: Telegram, RSS, optional search,
  downloads and user-triggered external review can send data over the network.
- Diagnostic records contain full text and Prompts; forgetting chat memory does not
  mean secure deletion of diagnostic records, WAL files, backups or Telegram copies.

## Experiments deliberately not shipped

- uzu / Mirai M4: no speculative decoding gains in the stock setup; custom shape
  configurations failed loading Weaver Hadamard factors. No new weights are included.
- Small reasoning model: six synthetic cases did not justify replacing the existing Observer.
- L0–L3 story-memory prototype: real-model recall still confused actors/completion stages.
- Prompt-search Harness: retired; not a hidden judge in the public pipeline.

These are scoped observations, not universal claims about the projects or models.
