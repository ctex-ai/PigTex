# PigTex Backend Scripts

## `benchmark_v1_stream.py`
Benchmark streaming quality for `/api/v1/chat/completions`.

### Metrics
- `ttft_ms`: time to first content chunk
- `total_ms`: end-to-end latency
- `jitter_p95_ms`: p95 inter-chunk delay
- `est_tokens_per_sec`: estimated generation throughput
- `fail_rate`: failed runs / total runs

### Usage
```powershell
python scripts/benchmark_v1_stream.py `
  --base-url http://127.0.0.1:3001 `
  --auth-token <JWT_TOKEN> `
  --api-key <PROVIDER_API_KEY> `
  --api-provider openai `
  --model <AVAILABLE_MODEL_FROM_/v1/models> `
  --runs 8 `
  --output-json benchmark-report.json
```

### Environment variable shortcuts
- `PIGTEX_BASE_URL`
- `PIGTEX_AUTH_TOKEN`
- `PIGTEX_API_KEY`
- `PIGTEX_API_BASE_URL`
- `PIGTEX_API_PROVIDER`
- `PIGTEX_MODEL`

Legacy compatibility:
- `--texapi-key`, `--texapi-base-url`
- `PIGTEX_TEXAPI_KEY`, `PIGTEX_TEXAPI_BASE_URL`

## `check_memory_continuity.py`
Synthetic 2-turn memory recall check:
1) store fact in turn-1
2) verify recall in turn-2

### Usage
```powershell
python scripts/check_memory_continuity.py `
  --base-url http://127.0.0.1:3001 `
  --auth-token <JWT_TOKEN> `
  --api-key <PROVIDER_API_KEY> `
  --api-provider openai `
  --model <AVAILABLE_MODEL_FROM_/v1/models> `
  --name Minh `
  --editor Neovim `
  --output-json memory-check.json
```

## `evaluate_prompt_training_wave.py`
Offline benchmark for prompt/skill training wave (no upstream API calls).

### Metrics
- `intent_detection_hit_rate_percent`
- weak/strong model `avg_score`, `p50_score`, `p95_score`
- weak/strong model `target_pass_rate_percent`
- required section coverage and prompt char distribution

### Usage
```powershell
python scripts/evaluate_prompt_training_wave.py `
  --cases ../../ops/observability/training/prompt_training_cases_v1.json `
  --weak-model gpt-4o-mini `
  --strong-model gpt-5-low `
  --output-json ../../ops/observability/reports/prompt-training-wave-latest.json
```

## `skill_foundry_cli.py`
Offline intake + compile pipeline cho kho prompt skill cạnh tranh.

### Commands
- `compile`: đọc raw `.md/.json`, normalize, cập nhật `data/skill_foundry/accepted_skill_store.json`, rồi build `data/skill_foundry/draft_registry.json` từ accepted corpus + batch mới
- `publish`: promote draft hiện tại thành `data/skill_foundry/runtime_registry.json`
- `registry`: xem active registry + draft registry + accepted skill store + challenger catalog
- `resolve`: kiểm tra runtime router sẽ match skill nào cho một message từ active registry
- `cleanup-rejected`: xóa cứng raw artifact trong `processed/rejected/` cũ hơn số ngày chỉ định

### Usage
```powershell
cd App_desktop/backend
.\venv\Scripts\python.exe scripts/skill_foundry_cli.py compile

.\venv\Scripts\python.exe scripts/skill_foundry_cli.py compile `
  --input marketing_repo `
  --dry-run

.\venv\Scripts\python.exe scripts/skill_foundry_cli.py publish `
  --released-by admin@pigtex.local `
  --note "reviewed and promoted"

.\venv\Scripts\python.exe scripts/skill_foundry_cli.py cleanup-rejected `
  --days 14

.\venv\Scripts\python.exe scripts/skill_foundry_cli.py resolve `
  --message "Viết hook Facebook Ads cho serum trị mụn"
```

### Optional judge model
- `PIGTEX_SKILL_FOUNDRY_JUDGE_MODEL`
- `PIGTEX_SKILL_FOUNDRY_API_KEY`
- `PIGTEX_SKILL_FOUNDRY_API_BASE_URL`
- `PIGTEX_SKILL_FOUNDRY_AUTO_ARCHIVE_ARTIFACTS`
- `PIGTEX_SKILL_FOUNDRY_REJECTED_RETENTION_DAYS`

## `benchmark_memory_self_learning_hard.py`
Hard production benchmark for memory + self-learning behavior.

### Stress scenarios
- Long-horizon recall under heavy distractor turns
- Conflict guard for single-value identity memory
- Explicit update convergence (`from now on` pattern)
- Workspace isolation for competing facts
- Temporary memory containment (same-conversation only)
- Noise immunity against transient queries (weather/price/news)
- Adaptive response-style learning persistence

### Competitive gates
- `score_percent >= 85`
- `scenario_pass_rate_percent >= 80`
- `critical_failures <= 0`

### Usage
```powershell
python scripts/benchmark_memory_self_learning_hard.py `
  --base-url http://127.0.0.1:3001 `
  --auth-token <JWT_TOKEN> `
  --api-key <PROVIDER_API_KEY> `
  --api-provider openai `
  --model <AVAILABLE_MODEL_FROM_/v1/models> `
  --noise-turns 10 `
  --output-json ../../ops/observability/reports/memory-self-learning-hard-latest.json
```

## `benchmark_production_full_system.py`
Production-grade full-system benchmark for PigTex.

### Coverage
- Availability and health
- Auth bootstrap and access control
- BYOK model discovery
- Non-stream chat contract and usage metadata
- Streaming contract and latency
- Memory continuity and temporary containment
- Workspace and user isolation
- Error semantics and fail-fast resilience
- Concurrent stability under load

### Competitive gates
- `score_percent >= 90`
- `domain_pass_rate_percent >= 85`
- `critical_failures <= 0`

### Usage
```powershell
python scripts/benchmark_production_full_system.py `
  --base-url http://127.0.0.1:3001 `
  --api-key <PROVIDER_API_KEY> `
  --api-base-url http://127.0.0.1:8045 `
  --api-provider custom `
  --model gpt-4o `
  --concurrent-requests 8 `
  --output-json ../../ops/observability/reports/pigtex-production-full-latest.json
```

### Environment variable shortcuts
- `PIGTEX_BASE_URL`
- `PIGTEX_API_KEY`
- `PIGTEX_API_BASE_URL`
- `PIGTEX_API_PROVIDER`
- `PIGTEX_MODEL`
- `PIGTEX_BENCH_PASSWORD`

Note:
- This script auto-registers ephemeral benchmark users and does not require `--auth-token`.
