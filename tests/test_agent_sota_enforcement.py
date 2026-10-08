import subprocess
import pytest
import re

POLICY_START_COMMIT = "c8016978b08c8c2ab8e2c84334d42b537f385f6a"

def test_arch_commits_have_sota_basis():
    """
    Validates compliance with AGENTS.md 3.7.5.
    Every consequential engineering commit (ARCH, INFRA, API, DOMAIN)
    added after the SOTA policy (c8016978) MUST contain a valid 'SOTA-Basis:' section.
    """
    # 1. Check history availability (Fail-closed design)
    try:
        # If POLICY_START_COMMIT is not in the history, this will throw an error.
        subprocess.run(["git", "rev-parse", "--verify", f"{POLICY_START_COMMIT}^{{commit}}"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        pytest.fail(f"Policy start commit {POLICY_START_COMMIT} is not in the local Git history. "
                    f"If running in CI, ensure checkout uses fetch-depth: 0.")

    # 2. Get the consequential commits
    try:
        result = subprocess.run(
            [
                "git", "log", f"{POLICY_START_COMMIT}..HEAD",
                "--extended-regexp", "--grep=^(ARCH|INFRA|API|DOMAIN):",
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

    commits = output.split("---COMMIT_SEP---")
    for commit_block in commits:
        commit_block = commit_block.strip()
        if not commit_block:
            continue
        
        lines = commit_block.splitlines()
        sha = lines[0]
        message = "\n".join(lines[1:])
        
        sota_idx = message.find("SOTA-Basis:")
        if sota_idx == -1:
            pytest.fail(f"Commit {sha} is a consequential engineering commit but lacks a 'SOTA-Basis:' footer. "
                        f"See AGENTS.md 3.7.5.")
            
        sota_block = message[sota_idx:]
        
        none_match = re.search(r'^SOTA-Basis:\s*None\s+(.+)', sota_block, re.IGNORECASE | re.DOTALL | re.MULTILINE)
        if none_match:
            justification = none_match.group(1).strip()
            if justification:
                continue
            else:
                pytest.fail(f"Commit {sha} declared 'SOTA-Basis: None' but provided no justification.")
        
        has_source = bool(re.search(r'^(?:1\.\s*)?Source(?:.*?):\s*[^\s]+', sota_block, re.IGNORECASE | re.MULTILINE))
        has_finding = bool(re.search(r'^(?:2\.\s*)?(?:Assumption|Finding)(?:.*?):\s*[^\s]+', sota_block, re.IGNORECASE | re.MULTILINE))
        has_recon = bool(re.search(r'^(?:3\.\s*)?Reconciliation(?:.*?):\s*[^\s]+', sota_block, re.IGNORECASE | re.MULTILINE))
        
        if not (has_source and has_finding and has_recon):
            pytest.fail(
                f"Commit {sha} has a malformed 'SOTA-Basis:' footer.\n"
                f"It must contain 'Source:', 'Assumption/Finding:', and 'Reconciliation:' fields with actual content, "
                f"or explicitly state 'SOTA-Basis: None' followed by a justification."
            )
