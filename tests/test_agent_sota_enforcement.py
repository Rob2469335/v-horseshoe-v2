import subprocess
import pytest
import re
import os

POLICY_START_COMMIT = "c8016978b08c8c2ab8e2c84334d42b537f385f6a"
KNOWN_LEGACY_VIOLATIONS = {
    "f287a472502fc88bc4dec6ab57a24d8f3190ad14",
    "b6df0171cc8210cfbfe9664e1e215d9f7eac2302",
    "c4fe0a8704913187396dea3a38f9d2dc8c169ffa",
    "0dd119eea7092ba2b18958a793ca4d538c78efd2"
}

def parse_sota_basis(message: str) -> bool:
    lines = message.splitlines()
    sota_starts = [i for i, line in enumerate(lines) if line.startswith('SOTA-Basis:')]
    
    if not sota_starts:
        return False
    if len(sota_starts) > 1:
        return False
        
    start_idx = sota_starts[0]
    first_line = lines[start_idx].strip()
    
    block_lines = []
    for line in lines[start_idx+1:]:
        stripped = line.strip()
        if not stripped:
            break
        # Stop at standard Git footers (e.g., Signed-off-by:), but don't stop at our own fields.
        if re.match(r'^[a-zA-Z0-9-]+:(\s|$)', stripped):
            if not re.match(r'^(?:1\.\s*)?Source:', stripped, re.IGNORECASE) and \
               not re.match(r'^(?:2\.\s*)?(?:Assumption|Finding):', stripped, re.IGNORECASE) and \
               not re.match(r'^(?:3\.\s*)?Reconciliation:', stripped, re.IGNORECASE) and \
               not re.match(r'^Justification:', stripped, re.IGNORECASE):
                break
        block_lines.append(stripped)
        
    full_block = '\n'.join(block_lines)
        
    if re.match(r'^SOTA-Basis:\s*None$', first_line, re.IGNORECASE):
        has_justification = bool(re.search(r'^Justification(?:.*?):[ \t]*(?=.*[a-zA-Z0-9]).+', full_block, re.IGNORECASE | re.MULTILINE))
        return has_justification
        
    has_source = bool(re.search(r'^(?:1\.\s*)?Source(?:.*?):\s*(?=.*[a-zA-Z].*[ \t]+https?://\S+|.*?https?://\S+[ \t]+.*[a-zA-Z]).+', full_block, re.IGNORECASE | re.MULTILINE))
    has_finding = bool(re.search(r'^(?:2\.\s*)?(?:Assumption|Finding)(?:.*?):\s*(?=.*[a-zA-Z0-9]).+', full_block, re.IGNORECASE | re.MULTILINE))
    has_recon = bool(re.search(r'^(?:3\.\s*)?Reconciliation(?:.*?):\s*(?=.*[a-zA-Z0-9]).+', full_block, re.IGNORECASE | re.MULTILINE))
    
    return has_source and has_finding and has_recon

def test_sota_parser_cases():
    cases = [
        ("ARCH: valid1\nSOTA-Basis:\nSource: Official Docs https://example.com\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid2\nSOTA-Basis: None\nJustification: Because it is a trivial change.", True),
        ("ARCH: valid3\nSOTA-Basis:\nSource: Some Title https://example.com\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid4\nSOTA-Basis:\nSource: https://example.com Some Title\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid5\nSOTA-Basis:\nSource: https://example.com Title\nFinding: Use AES-256-GCM.\nReconciliation: Updated to AES-256-GCM! (See #123)", True),
        ("ARCH: invalid6\nSome commit", False),
        ("ARCH: invalid7\nSOTA-Basis:", False),
        ("ARCH: invalid8\nSOTA-Basis: None", False),
        ("ARCH: invalid9\nSOTA-Basis: None\nSigned-off-by: user@example.com", False),
        ("ARCH: invalid10\nSOTA-Basis: None\nJustification: yes\n\nSome unrelated text here.", True),
        ("ARCH: invalid11\nSOTA-Basis: None\nSigned-off-by: user\nJustification: text", False),
        ("ARCH: invalid12\nSOTA-Basis:\nSource: https://example.com\nFinding: x\nReconciliation: y", False),
        ("ARCH: invalid13\nSOTA-Basis:\nSource: Some Title Here\nFinding: x\nReconciliation: y", False),
        ("ARCH: invalid14\nSOTA-Basis:\nSource: \nFinding: \nReconciliation: ", False),
        ("ARCH: invalid15\nSOTA-Basis:\nSoruce: a https://example.com\nFnding: x\nRecon: y", False),
        ("ARCH: invalid16\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nSOTA-Basis:", False),
        ("ARCH: valid17\nSOTA-Basis:\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nUnrelated text", True),
        ("ARCH: invalid18\nSOTA-Basis: None\nJustification:    ", False),
        ("ARCH: invalid19\nSOTA-Basis: None\nJustification: txt\nSOTA-Basis: None", False),
        ("ARCH: invalid20\nSOTA-Basis:\nSource: Title httpnotavalidurl\nFinding: x\nReconciliation: y", False),
        # Test consequential prefixes
        ("INFRA: valid\nSOTA-Basis: None\nJustification: text", True),
        ("API: valid\nSOTA-Basis: None\nJustification: text", True),
        ("DOMAIN: valid\nSOTA-Basis: None\nJustification: text", True),
        ("SERVICE: valid\nSOTA-Basis: None\nJustification: text", True),
        # Test legacy fail closed (None without Justification)
        ("ARCH: fail\nSOTA-Basis: None\nBecause we said so", False),
    ]
    for msg, expected in cases:
        assert parse_sota_basis(msg) == expected, f"Failed on:\n{msg}"

def run_scanner_on_repo(repo_dir, start_commit, end_commit):
    try:
        subprocess.run(["git", "rev-parse", "--verify", f"{start_commit}^{{commit}}"], check=True, capture_output=True, text=True, cwd=repo_dir)
    except subprocess.CalledProcessError:
        pytest.fail(f"Policy start commit {start_commit} is not in the local Git history. ")

    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", start_commit, end_commit], check=True, capture_output=True, cwd=repo_dir)
    except subprocess.CalledProcessError:
        pytest.fail(f"Policy start commit {start_commit} exists but is not an ancestor of {end_commit}. ")

    result = subprocess.run(
        [
            "git", "log", f"{start_commit}..{end_commit}",
            "--extended-regexp", "--grep=^(ARCH|INFRA|API|DOMAIN|SERVICE):",
            "--format=%H%n%B%n---COMMIT_SEP---"
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_dir
    )
    
    output = result.stdout.strip()
    if not output:
        return
        
    commits = output.split("---COMMIT_SEP---")
    for commit_block in commits:
        commit_block = commit_block.strip()
        if not commit_block:
            continue
            
        lines = commit_block.splitlines()
        sha = lines[0]
        
        if sha in KNOWN_LEGACY_VIOLATIONS:
            continue
            
        message = "\n".join(lines[1:])
        
        if not parse_sota_basis(message):
            pytest.fail(f"Commit {sha} has a missing or malformed 'SOTA-Basis:' footer. See AGENTS.md 3.7.5.")

def test_arch_commits_have_sota_basis():
    # Validates compliance against the current repository
    run_scanner_on_repo(os.getcwd(), POLICY_START_COMMIT, "HEAD")

def test_history_scanner_enforcement(tmp_path):
    # Setup temporary git repository
    repo = tmp_path / "repo"
    repo.mkdir()
    
    def git(*args):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
        
    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    
    (repo / "file.txt").write_text("init")
    git("add", "file.txt")
    git("commit", "-m", "init")
    init_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
    
    # 1. Commits with NON-GOVERNED prefixes (should be ignored by grep, even if SOTA block is missing/invalid)
    for prefix in ["FIX", "HEAL", "CI"]:
        (repo / "file.txt").write_text(prefix)
        git("commit", "-am", f"{prefix}: something\n\nNo SOTA section needed.")
        
    current_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
    # Scanner should pass because these prefixes are ignored
    run_scanner_on_repo(str(repo), init_sha, current_head)
    
    # 2. Commit with governed prefix and VALID SOTA block
    (repo / "file.txt").write_text("arch")
    git("commit", "-am", "ARCH: valid\n\nSOTA-Basis: None\nJustification: trivial")
    
    # 3. Test legacy exception mechanism
    (repo / "file.txt").write_text("legacy")
    git("commit", "-am", "ARCH: invalid\n\nSOTA-Basis: None\nbad text")
    invalid_legacy_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
    
    global KNOWN_LEGACY_VIOLATIONS
    original_violations = KNOWN_LEGACY_VIOLATIONS.copy()
    KNOWN_LEGACY_VIOLATIONS.add(invalid_legacy_sha)
    
    try:
        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
        run_scanner_on_repo(str(repo), init_sha, head_sha)
        
        # 4. Verify EVERY governed prefix is scanned and caught if invalid
        for prefix in ["ARCH", "INFRA", "API", "DOMAIN", "SERVICE"]:
            (repo / "file.txt").write_text(f"invalid_{prefix}")
            git("commit", "-am", f"{prefix}: invalid\n\nSOTA-Basis: None\nmissing justification")
            new_head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
            
            with pytest.raises(BaseException, match="missing or malformed"):
                run_scanner_on_repo(str(repo), init_sha, new_head_sha)
                
            # Hard reset to remove the invalid commit so we can test the next one
            git("reset", "--hard", head_sha)
            
    finally:
        KNOWN_LEGACY_VIOLATIONS = original_violations
