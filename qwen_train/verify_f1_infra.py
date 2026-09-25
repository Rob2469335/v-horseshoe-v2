"""verification_infrastructure_only — F1 infrastructure observability demonstration.

This is NOT an F1 observation. It demonstrates the observability path.
"""
import sys, os, json, tempfile
sys.path.insert(0, r'C:\Users\rober\Projects\v-horseshoe-v2\qwen_train')
os.environ['SWARM_WORKSPACE_ROOT'] = r'C:\Users\rober\Projects\swe_probe_work\f1_pilot\run_1_fresh\repo'
os.environ['SWARM_F1_NO_WEB_TOOLS'] = '1'

import f1_infra as f1i

print('[1] Process state model:')
for s in f1i.ProcessState:
    print(f'  {s.value}')

print('\n[2] Evidence directory:')
evidence_dir = f1i.EVIDENCE_DIR / 'verification_test'
evidence_dir.mkdir(parents=True, exist_ok=True)
print(f'  Created: {evidence_dir}')

print('\n[3] Health gate probe (current backend):')
backend_pid = f1i._check_port_listening(8000)
print(f'  Port 8000: {backend_pid is not None} (PID={backend_pid})')
health = f1i._probe_endpoint('http://127.0.0.1:8000/health', timeout=5.0)
print(f'  /health: {health}')

try:
    import urllib.request
    with urllib.request.urlopen('http://127.0.0.1:8000/status', timeout=5.0) as r:
        d = json.loads(r.read().decode())
        print(f'  /status ready: {d.get("ready")}')
        print(f'  workspace: {d.get("sandbox", {}).get("workspace_root")}')
except Exception as e:
    print(f'  /status: FAILED ({e})')

print('\n[4] File capture test:')
test_file = evidence_dir / 'test.log'
test_file.write_text('test output')
status = f1i._capture_file_status(str(test_file))
print(f'  exists={status["exists"]} writable={status["writable"]} non_empty={status["non_empty"]}')

print('\n[5] Manifest construction:')
backend_rec = f1i.ProcessRecord(
    role='backend', pid=backend_pid, expected_pid=backend_pid,
    start_time=f1i._now_iso(), command_identity='test',
    state=f1i.ProcessState.HEALTHY,
    stdout_path=str(evidence_dir / 'stdout.log'),
    stderr_path=str(evidence_dir / 'stderr.log'),
)
gate = f1i.HealthGateResult(
    passed=health and backend_pid is not None,
    timestamp=f1i._now_iso(),
    process_alive=backend_pid is not None,
    port_listening=backend_pid is not None,
    health_http_200=health,
    status_ready=True,
    expected_pid_identity=True,
    workspace_identity=r'C:\Users\rober\Projects\swe_probe_work\f1_pilot\run_1_fresh\repo',
)
manifest = f1i.build_manifest('verification_test', backend_rec, gate, evidence_dir)
manifest_path = f1i.save_manifest(manifest, evidence_dir)
print(f'  Manifest: {manifest_path} ({manifest_path.stat().st_size} bytes)')
print(f'  Health gate passed: {manifest.health_gate["passed"]}')
print(f'  Evidence preserved: {manifest.evidence_preservation["verified"]}')

print('\n[6] PID identity:')
print(f'  expected={backend_rec.expected_pid} actual={backend_rec.pid} match={backend_rec.expected_pid == backend_rec.pid}')

print('\nOBSERVABILITY DEMONSTRATION: COMPLETE')
print('verification_infrastructure_only — NOT an F1 observation.')
