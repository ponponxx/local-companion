# Contributing

Start with reproducible bugs in actor identity, location, clothing, event stages,
context budgets or installation. Use a fresh temporary data directory and a
fictional dialogue. State the expected state delta and observed state delta.

Run `uv run python -m unittest discover -s test -p 'test_*.py'` and
`uv run python scripts/check_public.py`. Do not add real chat logs, user IDs,
model weights, tokens or live-network tests. A test passing does not prove a
language model understood the event correctly. Keep decision rules and Writer
wording separate. Document any new model call and its latency cost.

Before a PR, review all staged files. Contributions are provided under Apache-2.0.
