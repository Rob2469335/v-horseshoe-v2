# N1 Harness Provenance Record

## Frozen Evaluation Harness

- **Repository path:** `qwen_train/run_twine_eval.py`
- **Original temp path:** `C:\Users\rober\AppData\Local\Temp\opencode\run_twine_eval.py`
- **SHA-256:** `C8388FF9C317944AA53C163C5149455B177F95F59A75DE7C1E13AD88AD041AC2`
- **File size:** 4947 bytes
- **Created:** 2026-09-22 16:50:50 EDT

## Execution Command

```
python "C:\Users\rober\AppData\Local\Temp\opencode\run_twine_eval.py"
```

## Official N1 Run IDs

| Attempt | Backend run_id | Timestamp |
|---------|---------------|-----------|
| 1 | `427f347e-d74a-459f-b1e6-8b1628934f51` | 2026-09-22 21:44:10 |
| 2 | `163567b5-1c46-43d5-9e89-65baa7f884b9` | 2026-09-22 21:54:14 |

## Task

- **task_id:** `pypa__twine-1066`
- **base_commit:** `4a1fc064a7899872ee845df6a8810bb51a6845ac`
- **model:** robs4b
- **routing:** local_only
- **outcome:** 1200s timeout, 9 trajectory steps, 0/3 F2P, no source modification

## Provenance

This script was manually created during the 2026-09-22 evaluation session
and executed from a temporary path. It is being preserved here for
reproducibility. N1 was NOT rerun to create this provenance record.

The script was not generated from a template, not copied from an existing
repository file, and not produced by any automated tool. It was hand-written
as a one-shot evaluation driver that reuses `run_curriculum._attempt_once()`
and `cli_baseline_swe` infrastructure.
