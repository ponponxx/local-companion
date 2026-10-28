# Public candidate validation

2026-10-28, macOS / Apple M4 / Python 3.12.15 / uv 0.13.0.

The release archive was extracted to a fresh temporary directory, without the
developer's runtime data, credentials, model cache or existing virtualenv.

- `uv sync --python 3.12 --locked`: passed.
- Synthetic unittest suite: **830 tests passed** in 13.33 seconds.
- Release-file heuristic scan: passed.
- Demo initialization: original Lin Qing profile, zero stored messages, empty
  user profile, proactive messages disabled, SQLite quick_check=ok.
- Existing database overwrite attempt: refused without changing its contents.
- Suggested model catalog lookup: one matching Q4_K_M GGUF file, filename
  selection tested for both dot and dash conventions; no weight download.
- Synthetic Prompt inspector: HTTP/UI checked for same-turn input/output and
  Writer display. It does not read live developer records in demo mode.

The initial world-name conversion produced duplicate place aliases; those were
removed before the successful suite. The initial model setup filename selector
expected a dot suffix and missed the suggested model's dash suffix; it now handles
both with an ambiguity check. No tests were removed to hide those failures.

No new model generation, private dialogue replay, external GPT call or Telegram
message was used for this public export. No clean-machine end-to-end Telegram or
media test was performed. These results establish packaging and synthetic
behavior, not the suggested model's conversational quality or broad OS support.
GitHub Actions has not run online; the repository has not yet been published.
