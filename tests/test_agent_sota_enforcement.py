import os
import re
import subprocess

import pytest

POLICY_START_COMMIT = "c8016978b08c8c2ab8e2c84334d42b537f385f6a"
KNOWN_LEGACY_VIOLATIONS = {
    "f287a472502fc88bc4dec6ab57a24d8f3190ad14",  # Missing source title
    "b6df0171cc8210cfbfe9664e1e215d9f7eac2302",  # Missing source title
}


def _has_bullet_source(block_lines: list[str]) -> bool:
    bullet_items: list[list[str]] = []
    current_bullet: list[str] = []
    for line in block_lines:
        stripped = line.strip()
        if re.match(r"^[-*•]\s+", stripped):
            if current_bullet:
                bullet_items.append(current_bullet)
            current_bullet = [stripped]
        elif current_bullet:
            current_bullet.append(stripped)
    if current_bullet:
        bullet_items.append(current_bullet)

    for item_lines in bullet_items:
        first_line = item_lines[0]
        if re.match(
            r"^[-*•]\s*(?:(?:1\.\s*)?Source|(?:2\.\s*)?(?:Assumption|Finding)|(?:3\.\s*)?Reconciliation):",
            first_line,
            re.IGNORECASE,
        ):
            continue
        combined = " ".join(item_lines)
        source_part = re.split(
            r"(?:\b|^)(?:Assumption|Finding|Reconciliation)(?:.*?):",
            combined,
            flags=re.IGNORECASE,
        )[0].strip()
        source_part = re.sub(r"^[-*•]\s+", "", source_part).strip()
        urls = re.findall(r"https?://\S+", source_part)
        if not urls:
            continue
        title_part = source_part
        for u in urls:
            title_part = title_part.replace(u, " ")
        title_part = title_part.strip()
        if not re.search(r"[a-zA-Z]", title_part):
            continue
        valid_urls = [
            u
            for u in urls
            if re.match(r"^https?://[a-zA-Z0-9][-a-zA-Z0-9.]*(?::\d+)?(?:/\S*)?$", u)
        ]
        if valid_urls:
            return True
    return False


def parse_sota_basis(message: str) -> bool:
    lines = message.splitlines()
    sota_starts = [i for i, line in enumerate(lines) if line.startswith("SOTA-Basis:")]

    if not sota_starts:
        return False
    if len(sota_starts) > 1:
        return False

    start_idx = sota_starts[0]
    first_line = lines[start_idx].strip()

    if re.match(r"^SOTA-Basis:\s*None$", first_line, re.IGNORECASE):
        if start_idx + 1 >= len(lines):
            return False

        next_line = lines[start_idx + 1].strip()

        justification_starts = [
            line
            for line in lines[start_idx + 1 :]
            if line.strip().lower().startswith("justification:")
        ]
        if len(justification_starts) > 1:
            return False

        return bool(
            re.match(r"^Justification:\s*(?=.*[a-zA-Z0-9]).+", next_line, re.IGNORECASE)
        )

    block_lines = []
    for line in lines[start_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            break
        # Stop at standard Git footers (e.g., Signed-off-by:), but don't stop at our own fields.
        if (
            re.match(r"^[a-zA-Z0-9-]+:(\s|$)", stripped)
            and not re.match(r"^(?:1\.\s*)?Source:", stripped, re.IGNORECASE)
            and not re.match(
                r"^(?:2\.\s*)?(?:Assumption|Finding):", stripped, re.IGNORECASE
            )
            and not re.match(r"^(?:3\.\s*)?Reconciliation:", stripped, re.IGNORECASE)
        ):
            break
        block_lines.append(stripped)

    full_block = "\n".join(block_lines)

    has_source = (
        bool(
            re.search(
                r"^(?:[-*•]\s*)?(?:1\.\s*)?Source(?:.*?):\s*(?=.*[a-zA-Z].*[ \t]+https?://\S+|.*?https?://\S+[ \t]+.*[a-zA-Z]).+",
                full_block,
                re.IGNORECASE | re.MULTILINE,
            )
        )
        or _has_bullet_source(block_lines)
    )
    has_finding = bool(
        re.search(
            r"(?:^|[-*•]\s*|\b)(?:2\.\s*)?(?:Assumption|Finding)(?:.*?):\s*(?=.*[a-zA-Z0-9]).+",
            full_block,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    has_recon = bool(
        re.search(
            r"(?:^|[-*•]\s*|\b)(?:3\.\s*)?Reconciliation(?:.*?):\s*(?=.*[a-zA-Z0-9]).+",
            full_block,
            re.IGNORECASE | re.MULTILINE,
        )
    )

    return has_source and has_finding and has_recon


def test_sota_parser_cases():
    cases = [
        (
            "ARCH: valid1\nSOTA-Basis:\nSource: Official Docs https://example.com\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: valid2\nSOTA-Basis: None\nJustification: Because it is a trivial change.",
            True,
        ),
        (
            "ARCH: valid3\nSOTA-Basis:\nSource: Some Title https://example.com\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: valid4\nSOTA-Basis:\nSource: https://example.com Some Title\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: valid5\nSOTA-Basis:\nSource: https://example.com Title\nFinding: Use AES-256-GCM.\nReconciliation: Updated to AES-256-GCM! (See #123)",
            True,
        ),
        ("ARCH: invalid6\nSome commit", False),
        ("ARCH: invalid7\nSOTA-Basis:", False),
        # 1. Valid SOTA-Basis: None with an immediate nonempty Justification:
        (
            "ARCH: valid_new1\nSOTA-Basis: None\nJustification: immediate justification.",
            True,
        ),
        # 2. Invalid intervening prose followed by a later justification.
        (
            "ARCH: invalid_new2\nSOTA-Basis: None\nSome intervening prose\nJustification: later",
            False,
        ),
        # 3. Invalid intervening Source: field followed by a later justification.
        (
            "ARCH: invalid_new3\nSOTA-Basis: None\nSource: https://example.com Title\nJustification: later",
            False,
        ),
        # 4. Missing justification.
        ("ARCH: invalid_new4\nSOTA-Basis: None\n", False),
        ("ARCH: invalid8\nSOTA-Basis: None", False),
        # 5. Empty justification.
        ("ARCH: invalid_new5\nSOTA-Basis: None\nJustification:", False),
        # 6. Whitespace-only justification.
        ("ARCH: invalid_new6\nSOTA-Basis: None\nJustification: \t \n", False),
        ("ARCH: invalid18\nSOTA-Basis: None\nJustification:    ", False),
        # 7. Duplicate justification fields.
        (
            "ARCH: invalid_new7\nSOTA-Basis: None\nJustification: first\nJustification: second",
            False,
        ),
        # 8. Duplicate SOTA-Basis: headers.
        (
            "ARCH: invalid_new8\nSOTA-Basis: None\nJustification: text\nSOTA-Basis: None",
            False,
        ),
        (
            "ARCH: invalid19\nSOTA-Basis: None\nJustification: txt\nSOTA-Basis: None",
            False,
        ),
        # 9. Malformed Justification labels.
        ("ARCH: invalid_new9\nSOTA-Basis: None\nJustification text", False),
        # 10. A Git footer where a justification is required.
        (
            "ARCH: invalid_new10\nSOTA-Basis: None\nSigned-off-by: user@example.com",
            False,
        ),
        ("ARCH: invalid9\nSOTA-Basis: None\nSigned-off-by: user@example.com", False),
        (
            "ARCH: invalid11\nSOTA-Basis: None\nSigned-off-by: user\nJustification: text",
            False,
        ),
        (
            "ARCH: invalid10\nSOTA-Basis: None\nJustification: yes\n\nSome unrelated text here.",
            True,
        ),
        (
            "ARCH: invalid12\nSOTA-Basis:\nSource: https://example.com\nFinding: x\nReconciliation: y",
            False,
        ),
        (
            "ARCH: invalid13\nSOTA-Basis:\nSource: Some Title Here\nFinding: x\nReconciliation: y",
            False,
        ),
        ("ARCH: invalid14\nSOTA-Basis:\nSource: \nFinding: \nReconciliation: ", False),
        (
            "ARCH: invalid15\nSOTA-Basis:\nSoruce: a https://example.com\nFnding: x\nRecon: y",
            False,
        ),
        (
            "ARCH: invalid16\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nSOTA-Basis:",
            False,
        ),
        (
            "ARCH: valid17\nSOTA-Basis:\nSource: Title https://example.com\nFinding: x\nReconciliation: y\n\nUnrelated text",
            True,
        ),
        (
            "ARCH: invalid20\nSOTA-Basis:\nSource: Title httpnotavalidurl\nFinding: x\nReconciliation: y",
            False,
        ),
        # Test consequential prefixes
        ("INFRA: valid\nSOTA-Basis: None\nJustification: text", True),
        ("API: valid\nSOTA-Basis: None\nJustification: text", True),
        ("DOMAIN: valid\nSOTA-Basis: None\nJustification: text", True),
        ("SERVICE: valid\nSOTA-Basis: None\nJustification: text", True),
        # Test legacy fail closed (None without Justification)
        ("ARCH: fail\nSOTA-Basis: None\nBecause we said so", False),
        # Markdown bullet source entries (positive cases)
        (
            "ARCH: bullet1\nSOTA-Basis:\n- Official Docs https://example.com\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: bullet2\nSOTA-Basis:\n- https://example.com Official Docs\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: bullet3\nSOTA-Basis:\n* PEP 735 Spec\n  https://packaging.python.org/pep735\nFinding: We should do X\nReconciliation: We did X",
            True,
        ),
        (
            "ARCH: bullet4\nSOTA-Basis:\n- PyPA PEP 735\n  https://packaging.python.org/pep735\n  Finding: We should do X\n  Reconciliation: We did X",
            True,
        ),
        # Markdown bullet source entries (negative cases)
        (
            "ARCH: bullet_neg1\nSOTA-Basis:\n- https://example.com\nFinding: X\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg2\nSOTA-Basis:\n- Title Only Without Link\nFinding: X\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg3\nSOTA-Basis:\n- Title With Invalid Link httpnotavalidurl\nFinding: X\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg4\nSOTA-Basis:\n- Title Incomplete Scheme http://\nFinding: X\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg5\nSOTA-Basis:\n- Unrelated prose text without any link\nFinding: X\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg6\nSOTA-Basis:\n- Valid Title https://example.com\nReconciliation: Y",
            False,
        ),
        (
            "ARCH: bullet_neg7\nSOTA-Basis:\n- Valid Title https://example.com\nFinding: X",
            False,
        ),
        (
            "ARCH: bullet_neg8\nSOTA-Basis:\n- \nFinding: X\nReconciliation: Y",
            False,
        ),
    ]
    for msg, expected in cases:
        assert parse_sota_basis(msg) == expected, f"Failed on:\n{msg}"


def run_scanner_on_repo(repo_dir, start_commit, end_commit, legacy_exemptions=None):
    if legacy_exemptions is None:
        legacy_exemptions = KNOWN_LEGACY_VIOLATIONS

    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", f"{start_commit}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
    except subprocess.CalledProcessError:
        pytest.fail(
            f"Policy start commit {start_commit} is not in the local Git history. "
        )

    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", start_commit, end_commit],
            check=True,
            capture_output=True,
            cwd=repo_dir,
        )
    except subprocess.CalledProcessError:
        pytest.fail(
            f"Policy start commit {start_commit} exists but is not an ancestor of {end_commit}. "
        )

    result = subprocess.run(
        [
            "git",
            "log",
            f"{start_commit}..{end_commit}",
            # Exclude MERGE commits from the authored-footer requirement. A merge
            # message routinely embeds the merged branch's PR title (e.g.
            # "Merge pull request #32 ...\n\nARCH: ..."), but the merge commit is
            # not an authored architectural change and must not be required to
            # carry its own SOTA-Basis footer. --no-merges filters ONLY the merge
            # commits themselves: authored commits reachable through a merge
            # (its second parent) still appear here and remain subject to the
            # requirement, so a merge can never hide a non-compliant authored
            # commit.
            "--no-merges",
            "--extended-regexp",
            # Recognise the scoped commit form too -- "ARCH(scope):" and its
            # siblings -- exactly as the commit-msg hook already accepts, for
            # every governed prefix.
            r"--grep=^(ARCH|INFRA|API|DOMAIN|SERVICE)(\([a-z0-9_-]+\))?:",
            "--format=%H%n%B%n---COMMIT_SEP---",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_dir,
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

        if sha in legacy_exemptions:
            continue

        message = "\n".join(lines[1:])

        if not parse_sota_basis(message):
            pytest.fail(
                f"Commit {sha} has a missing or malformed 'SOTA-Basis:' footer. See AGENTS.md 3.7.5."
            )


def test_arch_commits_have_sota_basis():
    # Validates compliance against the current repository
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    with patch("subprocess.Popen", new=_REAL_POPEN):
        run_scanner_on_repo(os.getcwd(), POLICY_START_COMMIT, "HEAD")


from _pytest.outcomes import Failed


def test_history_scanner_enforcement():
    import pathlib
    import tempfile
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        with patch("subprocess.Popen", new=_REAL_POPEN):
            _test_history_scanner_enforcement(tmp_path)


def _test_history_scanner_enforcement(tmp_path):
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
    init_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()

    # 1. Commits with NON-GOVERNED prefixes (should be ignored by grep, even if SOTA block is missing/invalid)
    for prefix in ["FIX", "HEAL", "CI"]:
        (repo / "file.txt").write_text(prefix)
        git("commit", "-am", f"{prefix}: something\n\nNo SOTA section needed.")

    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Scanner should pass because these prefixes are ignored
    run_scanner_on_repo(str(repo), init_sha, current_head)

    # 2. Commit with governed prefix and VALID SOTA block
    (repo / "file.txt").write_text("arch")
    git("commit", "-am", "ARCH: valid\n\nSOTA-Basis: None\nJustification: trivial")

    # 3. Test legacy exception mechanism (Case A - intentional exemption)
    (repo / "file.txt").write_text("legacy")
    git("commit", "-am", "ARCH: invalid\n\nSOTA-Basis: None\nbad text")
    invalid_legacy_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()

    # 4. Scanner should pass because the invalid commit is exempted
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Prove Case A passes ONLY because of the exemption
    with pytest.raises(Failed, match="missing or malformed 'SOTA-Basis:' footer"):
        run_scanner_on_repo(str(repo), init_sha, head_sha, legacy_exemptions=set())

    run_scanner_on_repo(
        str(repo), init_sha, head_sha, legacy_exemptions={invalid_legacy_sha}
    )

    # 4b. Case B - invalid lookalike is not exempted
    (repo / "file.txt").write_text("lookalike")
    git("commit", "-am", "ARCH: invalid lookalike\n\nSOTA-Basis: None\nlookalike text")
    invalid_lookalike_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()

    from tests.test_agent_sota_enforcement import KNOWN_LEGACY_VIOLATIONS

    # Prove the lookalike SHA is not in the exemption set
    assert invalid_lookalike_sha not in KNOWN_LEGACY_VIOLATIONS, (
        "Lookalike commit should not be exempted"
    )

    # We start from head_sha to exclude the preceding invalid commit (Case A).
    # Without this, the test would pass even if the lookalike was falsely exempted,
    # because it would trip over Case A instead.
    with pytest.raises(Failed, match="missing or malformed 'SOTA-Basis:' footer"):
        run_scanner_on_repo(
            str(repo),
            head_sha,
            invalid_lookalike_sha,
            legacy_exemptions=KNOWN_LEGACY_VIOLATIONS,
        )

    git("reset", "--hard", head_sha)

    # 5. Verify EVERY governed prefix is scanned and caught if invalid (without being exempted)
    for prefix in ["ARCH", "INFRA", "API", "DOMAIN", "SERVICE"]:
        (repo / "file.txt").write_text(f"invalid_{prefix}")
        git(
            "commit",
            "-am",
            f"{prefix}: invalid\n\nSOTA-Basis: None\nmissing justification",
        )
        new_head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=str(repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        with pytest.raises(Failed, match="missing or malformed"):
            run_scanner_on_repo(
                str(repo),
                init_sha,
                new_head_sha,
                legacy_exemptions={invalid_legacy_sha},
            )

        # Hard reset to remove the invalid commit so we can test the next one
        git("reset", "--hard", head_sha)

    # 6. Test missing baseline
    with pytest.raises(Failed, match="is not in the local Git history"):
        run_scanner_on_repo(
            str(repo), "nonexistent_sha", head_sha, legacy_exemptions=set()
        )

    # 7. Test disconnected baseline
    # We create an orphaned commit
    git("checkout", "--orphan", "orphan_branch")
    (repo / "file.txt").write_text("orphan")
    git("commit", "-am", "ARCH: orphan")
    orphan_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(Failed, match="is not an ancestor"):
        run_scanner_on_repo(str(repo), head_sha, orphan_sha, legacy_exemptions=set())


# --------------------------------------------------------------------------
# Scanner behaviour on synthetic histories: scoped prefixes, non-governed
# prefixes, and merge commits. These build throwaway repositories; the real
# repository history is never modified.
# --------------------------------------------------------------------------
GOVERNED_PREFIXES = ("ARCH", "INFRA", "API", "DOMAIN", "SERVICE")
NON_GOVERNED_PREFIXES = ("FIX", "CI", "DOCS")
VALID_FOOTER = "SOTA-Basis: None\nJustification: synthetic scanner test commit"
INVALID_FOOTER = "SOTA-Basis: None\nmissing justification"


def _sync_git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _rev(repo, ref="HEAD"):
    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _new_scanner_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _sync_git(repo, "init")
    _sync_git(repo, "config", "user.email", "test@example.com")
    _sync_git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("0")
    _sync_git(repo, "add", "tracked.txt")
    _sync_git(repo, "commit", "-m", "init")
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return _rev(repo), branch


def _commit(repo, subject, body, token):
    (repo / "tracked.txt").write_text(token)
    message = subject if not body else f"{subject}\n\n{body}"
    _sync_git(repo, "commit", "-am", message)
    return _rev(repo)


def test_scanner_covers_scoped_and_unscoped_governed_prefixes(tmp_path):
    """Every governed prefix is scanned in BOTH its ``PREFIX:`` and
    ``PREFIX(scope):`` forms, and an authored governed commit in either form
    without a valid footer is caught."""
    import pathlib
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    repo = pathlib.Path(tmp_path) / "scoped"
    with patch("subprocess.Popen", new=_REAL_POPEN):
        base, _branch = _new_scanner_repo(repo)

        for i, prefix in enumerate(GOVERNED_PREFIXES):
            _commit(repo, f"{prefix}: valid unscoped {i}", VALID_FOOTER, f"u{i}")
            _commit(repo, f"{prefix}(scope{i}): valid scoped {i}", VALID_FOOTER, f"s{i}")
        run_scanner_on_repo(str(repo), base, _rev(repo), legacy_exemptions=set())

        head = _rev(repo)
        for prefix in GOVERNED_PREFIXES:
            _commit(repo, f"{prefix}: invalid unscoped", INVALID_FOOTER, f"x{prefix}")
            with pytest.raises(
                Failed, match="missing or malformed 'SOTA-Basis:' footer"
            ):
                run_scanner_on_repo(
                    str(repo), base, _rev(repo), legacy_exemptions=set()
                )
            _sync_git(repo, "reset", "--hard", head)

            _commit(repo, f"{prefix}(scope): invalid scoped", INVALID_FOOTER, f"y{prefix}")
            with pytest.raises(
                Failed, match="missing or malformed 'SOTA-Basis:' footer"
            ):
                run_scanner_on_repo(
                    str(repo), base, _rev(repo), legacy_exemptions=set()
                )
            _sync_git(repo, "reset", "--hard", head)


def test_scanner_ignores_non_governed_prefixes(tmp_path):
    """``FIX:``, ``CI:`` and ``DOCS:`` are outside the SOTA footer policy."""
    import pathlib
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    repo = pathlib.Path(tmp_path) / "nongov"
    with patch("subprocess.Popen", new=_REAL_POPEN):
        base, _branch = _new_scanner_repo(repo)
        for i, prefix in enumerate(NON_GOVERNED_PREFIXES):
            _commit(repo, f"{prefix}: no footer required {i}", "", f"n{i}")
        run_scanner_on_repo(str(repo), base, _rev(repo), legacy_exemptions=set())


def test_scanner_merge_commit_body_needs_no_footer(tmp_path):
    """A merge whose body begins with ``ARCH:`` is not itself required to carry
    an authored-commit footer, provided the authored commits are compliant."""
    import pathlib
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    repo = pathlib.Path(tmp_path) / "merge_ok"
    with patch("subprocess.Popen", new=_REAL_POPEN):
        base, branch = _new_scanner_repo(repo)

        _sync_git(repo, "checkout", "-b", "feature")
        _commit(repo, "ARCH: valid feature commit", VALID_FOOTER, "fvalid")
        _sync_git(repo, "checkout", branch)
        _sync_git(
            repo,
            "merge",
            "--no-ff",
            "feature",
            "-m",
            "ARCH: merge feature branch\n\n(merge commit carries no SOTA footer)",
        )
        merge_sha = _rev(repo)

        # The merge body genuinely begins with a governed prefix...
        body = subprocess.run(
            ["git", "log", "-1", "--format=%B", merge_sha],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "ARCH:" in body

        # ...yet the scanner passes: the merge is excluded, its authored
        # ancestor carries a valid footer.
        run_scanner_on_repo(str(repo), base, merge_sha, legacy_exemptions=set())


def test_scanner_merge_cannot_hide_invalid_authored_commit(tmp_path):
    """An authored governed commit brought in THROUGH a merge is still scanned."""
    import pathlib
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    repo = pathlib.Path(tmp_path) / "merge_bad"
    with patch("subprocess.Popen", new=_REAL_POPEN):
        base, branch = _new_scanner_repo(repo)

        _sync_git(repo, "checkout", "-b", "feature")
        _commit(repo, "ARCH: invalid feature commit", INVALID_FOOTER, "finvalid")
        _sync_git(repo, "checkout", branch)
        _sync_git(
            repo,
            "merge",
            "--no-ff",
            "feature",
            "-m",
            "ARCH: merge feature branch\n\n(merge commit carries no SOTA footer)",
        )
        merge_sha = _rev(repo)

        # The invalid authored ARCH commit is reachable only via the merge's
        # second parent; excluding merge commits must not hide it.
        with pytest.raises(Failed, match="missing or malformed 'SOTA-Basis:' footer"):
            run_scanner_on_repo(str(repo), base, merge_sha, legacy_exemptions=set())
