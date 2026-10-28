# Architecture

```mermaid
flowchart TD
 TG[Telegram input] --> Q[SQLite inbox / 10-second message batching]
 Q --> S[Freeze current state / time progression]
 S --> M[StoryCore: select sourced memory / context budget]
 M --> O[User Observer: local model extracts events and intent]
 O --> V[Evidence checks / deterministic state reducer]
 V --> D[Decision core: needs, suitability, benefit and cost]
 D --> T[Delivery rules: tone lookup]
 D --> F[Writer facts and response outline]
 T --> W[Local Writer: final phrasing]
 F --> W
 W --> SEND[Telegram delivery]
 SEND --> B[Deferred response analysis / memory tasks]
 B --> DB[(Local SQLite memory and state)]
 DB --> M
 DB --> S
 LIFE[Schedule lookup / fictional life events] --> V
 LIFE --> P[Proactive selection / quiet hours / cooldown]
 P --> W
 V --> IMG[On-demand image prompt / independent worker]
 TRACE[Diagnostics: requests, outputs, timing, delivery] --> VIEW[Read-only Prompt inspector]
 O -.record.-> TRACE
 W -.record.-> TRACE
```

- **Observer** interprets text; output is not automatically a confirmed fact.
- **Reducer** owns formal location, clothing and event stages. A proposal is not arrival.
- **Decision core** chooses an action using explicit rules. Scores are engineering defaults.
- **Delivery core** maps traits and state to wording instructions, not a second action choice.
- **StoryCore** retrieves sourced past context. It is separate from formal current state.
- **Life subsystem** looks up the calendar and manages fictional event candidates.
- **Writer** phrases the selected plan; current small models can still violate it.
- **Diagnostics** are for inspection only, never a hidden source of chat memory.

The same local 9B server can serve Observer, Writer, memory extraction and background
calls sequentially. Deterministic gates and lookups do not call a language model.
Image, speech and optional embedding runtimes are separate. The experimental
L0–L3 memory implementation, Harness and uzu are not included.

The public export replaces the previous personalized setting with an authored
Starharbor world. Calendar place IDs are implementation keys, not travel facts.
