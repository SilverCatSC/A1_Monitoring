from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_monitoring_hermes_profile_is_local_and_has_no_tools() -> None:
    config = _read(ROOT / "config" / "hermes-monitoring.yaml")

    assert "api: http://127.0.0.1:18080/v1" in config
    assert "provider: custom:a1-local" in config
    assert "fallback_providers: []" in config
    assert "default: Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf" in config
    assert "Qwen3.5-9B-Q4_K_M.gguf" in config
    assert "supports_vision: true" in config
    assert "context_length: 32768" in config
    assert "disabled_toolsets:\n    - all" in config
    assert "platform_toolsets:\n  cli: []" in config


def test_ouroboros_internal_llm_profile_is_local_and_has_no_tools() -> None:
    config = _read(ROOT / "config" / "hermes-ouroboros-llm.yaml")

    assert "api: http://127.0.0.1:18080/v1" in config
    assert "provider: custom:a1-local" in config
    assert "fallback_providers: []" in config
    assert "default: Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf" in config
    assert "supports_vision: true" in config
    assert "disabled_toolsets:\n    - all" in config
    assert "platform_toolsets:\n  cli: []" in config


def test_ouroboros_profile_uses_hermes_and_disables_telemetry() -> None:
    config = _read(ROOT / "config" / "ouroboros-local.yaml.template")

    assert "llm:\n  backend: hermes" in config
    assert "orchestrator:\n  runtime_backend: hermes" in config
    assert "max_parallel_workers: 1" in config
    assert "use_worktrees: true" in config
    assert "stage3_enabled: false" in config
    assert "telemetry:\n  enabled: false" in config
    assert "verify_command_gate: block" in config


def test_maintenance_wrapper_separates_completion_from_runtime_profile() -> None:
    script = (ROOT / "scripts" / "hermes_maintenance_macos.sh").read_text(
        encoding="utf-8"
    )

    assert "--max-turns" in script
    assert "hermes_ouroboros_llm_home" in script
    assert "hermes_maintenance_home" in script
    assert "env -i" in script


def test_full_monitoring_runs_both_agents_and_head_table_audit() -> None:
    script = (ROOT / 'scripts' / 'run_full_monitoring_macos.sh').read_text(encoding='utf-8')

    assert 'run_head_table_audit_macos.sh' in script
    assert 'run_company_site_audit_macos.sh' in script
    assert 'run_ai_review_macos.sh' in script
    assert 'run_ouroboros_live_audit_macos.sh' in script
    assert 'start_local_ai.sh' in script
    assert 'ai_was_running=false' in script


def test_live_ouroboros_audit_is_read_only_and_local() -> None:
    script = (ROOT / 'scripts' / 'run_ouroboros_live_audit_macos.sh').read_text(encoding='utf-8')

    assert 'run_ouroboros_maintenance_macos.sh" qa' in script
    assert 'never access a browser, network, or other files' in script
    assert 'never propose automatic data or code changes' in script
    assert 'A vehicle can have no VIN' in script
    assert 'vision_expected' in script
    assert 'validate_staged_review.py' in script
    assert 'OUROBOROS_LIVE_AUDIT_UNRELIABLE' in script


def test_hermes_review_requires_vehicle_keys_from_the_evidence_packet() -> None:
    script = (ROOT / 'scripts' / 'run_ai_review_macos.sh').read_text(encoding='utf-8')

    assert '--allowed-vehicles-from "$INPUT_FILE"' in script
    assert 'build_ai_work_units.py' in script
    assert 'run_ai_work_units.py' in script
    assert 'validate_staged_review.py' in script


def test_local_model_server_is_low_concurrency_and_multimodal() -> None:
    script = _read(ROOT / 'scripts' / 'start_local_ai.sh')
    lock = _read(ROOT / 'config' / 'ai-tools.lock')

    assert '--mmproj "$MMPROJ_PATH"' in script
    assert '-np 1' in script
    assert '-t "$AI_MODEL_THREADS"' in script
    assert '-n "$AI_MODEL_MAX_OUTPUT_TOKENS"' in script
    assert '--poll 0' in script
    assert '--reasoning auto' in script
    assert '--reasoning-budget "$AI_MODEL_REASONING_BUDGET"' in script
    assert '--prio -1' not in script
    assert 'AI_MODEL_REPO=ggml-org/Qwen2.5-VL-3B-Instruct-GGUF' in lock
    assert 'AI_MODEL_FILE=Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf' in lock
    assert 'AI_MODEL_MMPROJ_FILE=mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf' in lock
    assert 'AI_MODEL_THREADS=4' in lock
    assert 'AI_MODEL_CONTEXT=32768' in lock
    assert 'AI_MODEL_MAX_OUTPUT_TOKENS=512' in lock
    assert 'AI_MODEL_REASONING_BUDGET=128' in lock
    assert 'AI_HEAVY_MODEL_REPO=unsloth/Qwen3.5-9B-GGUF' in lock
    assert 'AI_HEAVY_MODEL_FILE=Qwen3.5-9B-Q4_K_M.gguf' in lock


def test_staged_runner_reserves_reasoning_for_final_synthesis() -> None:
    script = _read(ROOT / 'scripts' / 'run_ai_work_units.py')

    assert "_data_prompt(unit), reasoning='none'" in script
    assert "reasoning='none'," in script
    assert "_summary_prompt(compact), reasoning='low'" in script
