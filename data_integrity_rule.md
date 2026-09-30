<!-- BENCHMARK-DATA-INTEGRITY-BEGIN -->

## Mandatory Benchmark Data Integrity Rule

This rule applies to EVERY individual SWE/CLI test and rollout.

### Network/data acquisition is part of infrastructure

A benchmark may require access to Hugging Face or another dataset/source to obtain the repository, patch, test patch, metadata, or other required task artifacts.

If that acquisition fails:

* classify the failure as infrastructure;
* do NOT classify it as a model/capability failure;
* do NOT silently substitute another data source;
* do NOT modify the benchmark harness;
* do NOT invent or reconstruct missing benchmark data;
* do NOT remove required tests;
* do NOT change `fail_to_pass`;
* do NOT change `pass_to_pass`;
* do NOT change the base commit;
* do NOT apply a guessed patch;
* do NOT continue a measured run using incomplete task data.

### Retry policy

A transient network failure may be retried a limited number of times.

After the configured retry limit is reached, STOP the test and record:

`infra_failure = true`

The test must not be counted in capability metrics.

Do not repeatedly retry the same failed network request indefinitely.

### Local pool data does not automatically replace missing harness data

Even if `swe_pool.jsonl` or `click_only.jsonl` contains fields such as:

* `instance_id`
* `base_commit`
* `fail_to_pass`
* `pass_to_pass`
* `test_cmd`
* `test_patch`

the agent must NOT assume that the existing harness is permitted to bypass its normal dataset acquisition path.

First determine how the benchmark harness is designed to obtain and validate the task artifacts.

Any change to that behavior must be made outside the measured run and explicitly documented.

### Measurement rule

A test is measured only when:

1. worker startup passed;
2. backend startup passed;
3. workspace verification passed;
4. required task data was successfully acquired;
5. the repository was prepared at the correct base commit;
6. the required test patch was successfully applied;
7. the benchmark actually reached the model/repair stage.

If any prerequisite fails before the model is given a valid task:

`measurement_valid = false`

and

`infra_failure = true`

### Never turn infrastructure recovery into model evaluation

Do not count:

* Hugging Face failures;
* DNS failures;
* connection resets;
* repository download failures;
* workspace failures;
* backend failures;
* llama.cpp failures;
* missing task artifacts;
* harness crashes;

as model failures.

Fix the infrastructure first, then run the actual benchmark.

### No silent benchmark changes

The agent must never respond to an infrastructure failure by changing the benchmark definition.

For example, it must not:

`HF unavailable → use guessed local patch → run Click → count result`

Instead:

`HF unavailable → classify infrastructure failure → stop → repair acquisition path → rerun cleanly`

This rule applies independently to every test.

### Updated verification sequence

Every test becomes:

```text
1. Verify Qwen GGUF
2. Verify SHA256
3. Verify llama.cpp configuration
4. Verify port 8080
5. Verify llama health
6. Verify model response
7. Verify backend /readyz
8. Verify workspace
9. Verify benchmark data acquisition
10. Verify base commit
11. Verify test patch
12. Verify F2P/P2P lists
13. ONLY THEN give task to worker
```

<!-- BENCHMARK-DATA-INTEGRITY-END -->