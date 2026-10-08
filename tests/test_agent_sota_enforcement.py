import subprocess
import pytest

POLICY_START_COMMIT = "c8016978b08c8c2ab8e2c84334d42b537f385f6a"

def test_arch_commits_have_sota_basis():
    """
    Validates compliance with AGENTS.md 3.7.5.
    Every ARCH: commit added after the SOTA policy (c8016978)
    MUST contain a 'SOTA-Basis:' section.
    """
    try:
        # Get all ARCH: commits since the policy was introduced
        result = subprocess.run(
            ["git", "log", f"{POLICY_START_COMMIT}..HEAD", "--grep=^ARCH:", "--format=%H%n%B%n---COMMIT_SEP---"],
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
        if "SOTA-Basis:" not in message:
            pytest.fail(
                f"Commit {sha} is an ARCH: commit but lacks a 'SOTA-Basis:' footer. "
                "Per AGENTS.md 3.7.5, all architectural decisions must document their SOTA basis "
                "or explicitly declare 'SOTA-Basis: None' with justification."
            )
