import subprocess
import pytest
import re

POLICY_START_COMMIT = "c8016978b08c8c2ab8e2c84334d42b537f385f6a"

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
               not re.match(r'^(?:3\.\s*)?Reconciliation:', stripped, re.IGNORECASE):
                break
        block_lines.append(stripped)
        
    if re.match(r'^SOTA-Basis:\s*None$', first_line, re.IGNORECASE):
        justification = ' '.join(block_lines).strip()
        return bool(justification)
        
    full_block = '\n'.join(block_lines)
    
    has_source = bool(re.search(r'^(?:1\.\s*)?Source(?:.*?):\s*(?=.*[a-zA-Z].*[ \t]+https?://\S+|.*?https?://\S+[ \t]+.*[a-zA-Z]).+', full_block, re.IGNORECASE | re.MULTILINE))
    has_finding = bool(re.search(r'^(?:2\.\s*)?(?:Assumption|Finding)(?:.*?):\s*(?=.*[a-zA-Z0-9]).+', full_block, re.IGNORECASE | re.MULTILINE))
    has_recon = bool(re.search(r'^(?:3\.\s*)?Reconciliation(?:.*?):\s*(?=.*[a-zA-Z0-9]).+', full_block, re.IGNORECASE | re.MULTILINE))
    
    return has_source and has_finding and has_recon

def test_sota_parser_cases():
    cases = [
        ("ARCH: valid1\nSOTA-Basis:\nSource: Official Docs https://example.com\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid2\nSOTA-Basis: None\nBecause it is a trivial change.", True),
        ("ARCH: valid3\nSOTA-Basis:\nSource: Some Title https://example.com\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid4\nSOTA-Basis:\nSource: https://example.com Some Title\nFinding: We should do X\nReconciliation: We did X", True),
        ("ARCH: valid5\nSOTA-Basis:\nSource: https://example.com Title\nFinding: Use AES-256-GCM.\nReconciliation: Updated to AES-256-GCM! (See #123)", True),
        ("ARCH: invalid6\nSome commit", False),
        ("ARCH: invalid7\nSOTA-Basis:", False),
        ("ARCH: invalid8\nSOTA-Basis: None", False),
        ("ARCH: invalid9\nSOTA-Basis: None\nSigned-off-by: user@example.com", False),
        ("ARCH: invalid10\nSOTA-Basis: None\n\nSome unrelated text here.", False),
        ("ARCH: invalid11\nSOTA-Basis: None\nSigned-off-by: user\nSome unrelated text", False),
        ("ARCH: invalid12\nSOTA-Basis:\nSource: https://example.com\nFinding: x\nReconciliation: y", False),
        ("ARCH: invalid13\nSOTA-Basis:\nSource: Some Title Here\nFinding: x\nReconciliation: y", False),
        ("ARCH: invalid14\nSOTA-Basis:\nSource: \nFinding: \nReconciliation: ", False),
        ("ARCH: invalid15\nSOTA-Basis:\nSoruce: a https://example.com\nFnding: x\nRecon: y", False),
        ("ARCH: invalid16\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nSOTA-Basis:", False),
        ("ARCH: valid17\nSOTA-Basis:\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nUnrelated text", True),
        ("ARCH: invalid18\nSOTA-Basis: None\n   ", False),
        ("ARCH: invalid19\nSOTA-Basis: None\njustification\nSOTA-Basis: None", False),
        ("ARCH: invalid20\nSOTA-Basis:\nSource: Title httpnotavalidurl\nFinding: x\nReconciliation: y", False),
        # Test consequential prefixes
        ("INFRA: valid\nSOTA-Basis: None\nJustified", True),
        ("API: valid\nSOTA-Basis: None\nJustified", True),
        ("DOMAIN: valid\nSOTA-Basis: None\nJustified", True),
        ("SERVICE: valid\nSOTA-Basis: None\nJustified", True)
    ]
    for msg, expected in cases:
        assert parse_sota_basis(msg) == expected, f"Failed on:\n{msg}"

def test_arch_commits_have_sota_basis():
    """
    Validates compliance with AGENTS.md 3.7.5.
    Every consequential engineering commit (ARCH, INFRA, API, DOMAIN, SERVICE)
    added after the SOTA policy (c8016978) MUST contain a valid 'SOTA-Basis:' section.
    """
    try:
        subprocess.run(["git", "rev-parse", "--verify", f"{POLICY_START_COMMIT}^{{commit}}"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        pytest.fail(f"Policy start commit {POLICY_START_COMMIT} is not in the local Git history. "
                    f"If running in CI, ensure checkout uses fetch-depth: 0.")

    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", POLICY_START_COMMIT, "HEAD"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pytest.fail(f"Policy start commit {POLICY_START_COMMIT} exists but is not an ancestor of HEAD. "
                    f"Ensure you are checking the correct branch history.")

    try:
        result = subprocess.run(
            [
                "git", "log", f"{POLICY_START_COMMIT}..HEAD",
                "--extended-regexp", "--grep=^(ARCH|INFRA|API|DOMAIN|SERVICE):",
                "--format=%H%n%B%n---COMMIT_SEP---"
            ],
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Failed to run git log: {e.stderr}")

    output = result.stdout.strip()
    if not output:
        return

    KNOWN_LEGACY_VIOLATIONS = {
        "f287a472502fc88bc4dec6ab57a24d8f3190ad14",
        "b6df0171cc8210cfbfe9664e1e215d9f7eac2302",
        "c4fe0a8704913187396dea3a38f9d2dc8c169ffa"
    }

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
