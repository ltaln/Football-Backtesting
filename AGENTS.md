# Insight AI · Luna Maintenance Layer

This repository's production backtest model is frozen by default. The maintenance layer exists only to reduce development cost and token usage; it must not change model behavior unless the user explicitly requests a model change.

## Default routing

Use GPT-5.6 Sol only for architecture, ambiguous multi-module reasoning, model/prompt/evaluation-policy decisions, consequential changes, and final acceptance.

Delegate routine engineering work to Luna before Sol reads large repository context:

- repository exploration and call-path tracing -> `luna-scout`
- logs, status codes, schemas, cursors, task IDs, payloads, and bounded bug diagnosis -> `luna-debugger`
- small, clearly scoped implementation fixes -> `luna-patcher`
- focused tests, regression checks, syntax/import/schema verification -> `luna-tester`

Do not spawn agents just because concurrency is available. Use one Luna by default; use two only for genuinely independent work. Never exceed the project concurrency limit.

## Context firewall

The parent Sol thread should not reread large files when a Luna agent can inspect them. Luna handoffs must be compact and contain only:

- ROOT_CAUSE
- FILES_AND_LINES
- PATCH_SCOPE
- TESTS
- MODEL_IMPACT
- RISK
- ESCALATION_NEEDED

Stop gathering context once the evidence is sufficient to act.

## Frozen model boundary

Unless the user's current request explicitly asks to change model behavior, treat the following as protected and read-only:

- `prompts/**`
- prediction prompt semantics and frozen prompt binding
- the 13-module meaning/order and model conclusions
- evaluation definitions and scoring policy in `evaluation/**`
- prediction weights, thresholds, calibration policy, or model version rules
- replay time-isolation / result-masking policy
- Prediction Commit semantics
- the requirement that final backtest analysis, summary, and recommendations are owned by GPT

If a requested maintenance fix appears to require changing any protected behavior, STOP and escalate to Sol instead of modifying it.

## Patch budget

For ordinary Luna maintenance patches:

- modify at most 3 source files per task
- keep the patch under about 150 changed lines unless the task explicitly requires more
- no unrelated cleanup, renaming, refactoring, formatting sweeps, dependency upgrades, or architecture changes
- preserve API compatibility unless the user explicitly requests an API change
- add or update only the focused regression test needed for the bug

If the fix cannot stay within this budget, return a diagnosis and escalate.

## Failure budget

A Luna agent may make at most two materially different attempts to solve the same issue. After two failed attempts, stop consuming tokens and escalate to Sol with the compact evidence gathered so far.

## Verification

Before a maintenance patch is accepted:

1. run the narrowest relevant test first;
2. run broader tests only when the change can affect shared behavior;
3. compare the final diff against the task scope;
4. explicitly state whether model behavior changed. For normal maintenance, this must be `NO`.

Production backtests must never depend on these agents. The agents are development tools only.