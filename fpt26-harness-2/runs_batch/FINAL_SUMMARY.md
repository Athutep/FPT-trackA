# FINAL_SUMMARY — deepseek-v4-pro

- date: 2026-08-05 18:18
- result: **3/3 success**, retry_ids=[]
- coverage: 3/3 (OK)
- real_api_only: True (backend=OpenRouterClient, per-task clients)
- total score (correctness-gated PPA): 7.20
- wall time: 0.68 h
- LLM tokens: 57927 total (11446 prompt + 46481 completion, 9344 cached)
- LLM api: 8 calls, 2 failed attempts

## Failed / incomplete tasks

| task_id | type | error / phase | score |
|---|---|---|---|

## All results

| task_id | pass | synth | score | accel | credits | wall_s |
|---|---|---|---|---|---|---|
| dotProduct_optimize | PASS | True | 3.0 | 28.53x | 20/40 | 1857.4 |
| projection_bugfix | PASS | True | 1.4 | - | 6/20 | 95.6 |
| residual_stream_deadlock | PASS | True | 2.8 | - | 56/80 | 483.2 |