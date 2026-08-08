import json
import ast
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import logging
log = logging.getLogger("zenith_cli")

FAILURE_TAXONOMY = {
    "import_resolution": {"patterns": [r"importerror", r"modulenotfounderror", r"no module named"], "tier": 0},
    "syntax_error": {"patterns": [r"syntaxerror", r"indentationerror", r"invalid syntax", r"unexpected eof", r"unexpected indent", r"expected an indented block"], "tier": 0},
    "type_error": {"patterns": [r"typeerror", r"unsupported operand type", r"argument of type"], "tier": 1},
    "runtime_exception": {"patterns": [r"keyerror", r"indexerror", r"attributeerror", r"valueerror"], "tier": 1},
    "tool_misuse": {"patterns": [r"unknown tool", r"invalid tool call", r"missing required parameter"], "tier": 1},
    "specification_drift": {"patterns": [r"deviat", r"not what was asked", r"unrelated", r"specification drift", r"spec drift", r"requirement drift"], "tier": 2},
    "logic_bug": {"patterns": [r"wrong result", r"incorrect", r"off-by-one", r"edge case"], "tier": 2},
    "api_failure": {"patterns": [r"connectionerror", r"timeout", r"5\d{2}", r"service unavailable"], "tier": 2},
}

KNOWLEDGE_BASE_DIR = Path(__file__).parent.parent.parent / "swarm_os" / "healing"
CURES_FILE = KNOWLEDGE_BASE_DIR / "distilled_cures.json"
LESSONS_FILE = KNOWLEDGE_BASE_DIR / "repair_lessons.jsonl"

# ---------------------------------------------------------------------------
# Constitutional guards (defense-in-depth for autonomous code repair).
# Per OWASP AI-Agent / SafeAgent / Zeltrex best practice these are enforced in
# CODE, not prompts: the LLM is treated as untrusted.
# ---------------------------------------------------------------------------

_REPO_ROOT = KNOWLEDGE_BASE_DIR.parent.parent

# R6: only repair inside source trees. Everything else (tests, config, docs,
# model files, build/pipeline files, .env) is off-limits for auto-repair.
# 2026: the ceiling is now the WRITTEN autonomy policy (autonomy_policy.json),
# loaded at startup via swarm_os/services/autonomy_policy.py — the code enforces
# the policy file and does NOT duplicate it (the old `src/` entry here went stale
# precisely because this constant drifted from the written intent). _is_repairable_path
# resolves the actual allowed dirs from the policy; these module constants remain
# only as the degraded fail-closed fallback if the policy cannot be loaded.
REPAIR_ALLOWED_DIRS = (
    _REPO_ROOT / "swarm_os",
    _REPO_ROOT / "runtime_v2",
    _REPO_ROOT / "organism_console",
)

# Blocked path patterns matched against any path component (case-insensitive).
REPAIR_BLOCKED_PATTERNS = (
    "tests", "test_", "conftest", ".env", ".git", ".venv", "venv", "node_modules",
    "models", "docs", "scripts", ".github", "data", "logs", "_data",
    "AGENTS.md", "README", "package.json", "package-lock.json", "pyproject.toml",
    "requirements", "pytest.ini", "tox.ini", "start-dev", "start_llama",
)

# R4 (anti-truncation): a full-file LLM rewrite that shrinks the file more than
# 20% is presumed truncated and rejected for human review instead of applied.
ANTI_TRUNCATION_RATIO = 0.8

# R17-R19 (operational limits): daily repair cap + 3-strike circuit breaker.
MAX_DAILY_REPAIRS = int(os.getenv("MAX_DAILY_REPAIRS", "50"))
MAX_CONSECUTIVE_FAILURES = 3
BREAK_COOLDOWN_SECONDS = 4 * 3600  # 4h pause after the breaker trips
BREAKER_FILE = KNOWLEDGE_BASE_DIR / "repair_breaker.json"


def _is_repairable_path(file_path: Optional[Path]) -> bool:
    """Allowlist source dirs + blocklist sensitive paths (constitutional R1-R7).

    2026: consults the written autonomy policy first (single source of truth —
    directory-level + dependency-aware self-modify block). Falls back to the
    module constants ONLY if the policy cannot be loaded, and that fallback is
    fail-closed (missing policy -> not repairable)."""
    if not file_path:
        return False
    try:
        from swarm_os.services.autonomy_policy import get_autonomy_policy
        policy = get_autonomy_policy()
        if policy is not None:
            return policy.is_repairable(file_path)
    except Exception:
        pass
    # Degraded fail-closed fallback (policy missing/unloadable).
    try:
        resolved = Path(file_path).resolve()
    except Exception:
        return False
    if resolved.suffix != ".py":
        return False
    try:
        if str(resolved).startswith(str(KNOWLEDGE_BASE_DIR.resolve())):
            return False
    except Exception:
        pass
    parts = [p.lower() for p in resolved.parts]
    for pattern in REPAIR_BLOCKED_PATTERNS:
        if any(pattern in p for p in parts):
            return False
    for allowed in REPAIR_ALLOWED_DIRS:
        try:
            if str(resolved).startswith(str(Path(allowed).resolve())):
                return True
        except Exception:
            continue
    return False


def _anti_truncation_ok(original: str, new: str, min_ratio: float = ANTI_TRUNCATION_RATIO) -> bool:
    """Reject LLM rewrites that silently shrink a file (truncated output)."""
    if len(original) < 200:
        return True  # tiny files: size heuristic is unreliable
    return len(new) >= int(len(original) * min_ratio)


def _load_breaker() -> Dict[str, Any]:
    if BREAKER_FILE.exists():
        try:
            return json.loads(BREAKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_breaker(state: Dict[str, Any]):
    try:
        KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
        # 2026 (watch-loop coexistence): the breaker file is shared state (daily
        # cap + circuit-open timestamps). Serialize the write path with the same
        # filelock primitive used for AGENTS.md / auto_repairs so a read-modify-
        # write from two engines can never lose an increment or race the trip
        # threshold. The CLI watchman is gated off when SWARM_AUTONOMY=1, so this
        # is belt-and-suspenders — but the write must never be the weak link.
        from filelock import FileLock
        with FileLock(str(BREAKER_FILE) + ".lock", timeout=5.0):
            BREAKER_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to persist circuit breaker state: %s", e)
        pass


def _circuit_state() -> Dict[str, Any]:
    state = _load_breaker()
    today = date.today().isoformat()
    if state.get("date") != today:
        state = {"date": today, "repairs": 0, "consecutive_failures": 0, "open_until": 0.0}
    return state


def _circuit_allows_repair() -> Tuple[bool, Optional[str]]:
    """Fail closed when the breaker is open or the daily cap is hit."""
    state = _circuit_state()
    if time.time() < float(state.get("open_until", 0.0)):
        return False, "circuit open (3 consecutive failures — pausing 4h)"
    if int(state.get("repairs", 0)) >= MAX_DAILY_REPAIRS:
        return False, f"daily repair cap reached ({MAX_DAILY_REPAIRS})"
    return True, None


def _record_repair_result(success: bool):
    state = _circuit_state()
    state["repairs"] = int(state.get("repairs", 0)) + 1
    if success:
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        if state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
            state["open_until"] = time.time() + BREAK_COOLDOWN_SECONDS
            state["consecutive_failures"] = 0
            log.warning("Repair circuit breaker tripped: %d consecutive failures. Pausing %dh.",
                        MAX_CONSECUTIVE_FAILURES, BREAK_COOLDOWN_SECONDS // 3600)
    _save_breaker(state)


def _find_related_tests(file_path: Path) -> List[Path]:
    """Locate tests that exercise the repaired module (quality gate).

    Matches by filename AND by content (module path or basename imported), so a
    test named test_foo.py that imports runtime_v2/services/fallback_manager is
    found for fallback_manager.py even though the names differ."""
    tests_dir = _REPO_ROOT / "tests"
    if not tests_dir.exists():
        return []
    try:
        rel = Path(file_path).resolve().relative_to(_REPO_ROOT.resolve())
    except Exception:
        return []
    module_base = rel.stem
    module_path = str(rel).replace("\\", "/").replace(".py", "")
    related = []
    for t in sorted(tests_dir.glob("test_*.py")):
        if module_base in t.name or t.name.replace("test_", "").replace(".py", "") in module_base:
            related.append(t)
            if len(related) >= 5:
                break
        else:
            try:
                head = t.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                continue
            if module_path in head or module_base in head:
                related.append(t)
                if len(related) >= 5:
                    break
    return related


def _run_related_tests(file_path: Path) -> Optional[Tuple[bool, str]]:
    """Run the repaired module's own tests. Returns None when no tests exist."""
    related = _find_related_tests(file_path)
    if not related:
        return None
    cmd = [sys.executable, "-m", "pytest", *[str(t) for t in related], "-q", "--tb=line"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                              encoding="utf-8", errors="replace")
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "related test run timed out after 90s"
    except Exception as exc:
        return False, f"related test run failed: {exc}"

def classify_failure(error_text: str) -> Tuple[str, int]:
    error_lower = error_text.lower()
    for failure_type, info in FAILURE_TAXONOMY.items():
        for pattern in info["patterns"]:
            if re.search(pattern, error_lower):
                return failure_type, info["tier"]
    return "unknown", 2


# PS/MV fix_class (2026 self-healing: diagnose BEFORE patching). Mirrors the
# Diagnostician in swarm_os/healing (kept local here to avoid a cross-package
# import from the CLI layer). A failure classified `model_variability` means the
# model itself cannot perform the task — retrying/LLM-patching is wasted spend,
# so the repair orchestrator skips the expensive T2 LLM call and records why.
#
# IMPORTANT (2026 L2 review): "cannot" / "unable to" are intentionally NOT in
# FIX_MV_TERMS. Those words appear constantly in PATCHABLE Python tracebacks
# (e.g. "cannot unpack non-iterable NoneType object", "cannot import name 'X'",
# "unable to serialize object") — treating them as model_variability would skip
# real fixes. FIX_PS_TERMS (structural/schema signals) are checked FIRST and win
# ties; MV only fires when no structural signal matched.
FIX_PS_TERMS = (
    "json", "format", "invalid", "malformed", "forbidden", "unauthorized",
    "not allowed", "missing field", "syntax error", "parse", "schema",
    "expected", "must be", "should not", "violation", "rule", "constraint",
    "not defined", "nameerror", "typeerror", "attributeerror", "importerror",
    "keyerror", "indexerror", "valueerror", "traceback",
)
# MV fires ONLY when no structural/PS signal matched. Keep only signals that are
# genuinely about the MODEL being the limitation, not generic traceback language.
FIX_MV_TERMS = (
    "hallucin", "don't know", "i don't know", "cannot reason", "wrong answer",
    "misunderstanding", "nonsense", "not capable", "out of scope",
)


def classify_fix_class(error_text: str) -> str:
    """Classify a failure as prompt_sensitivity vs model_variability.

    PS = fixable by a prompt/rule/small-patch change (cheap to attempt).
    MV = the model can't do it; retry/LLM-patch is wasted (escalate/record).

    Structural/schema signals (PS) are checked FIRST and win ties, so a code
    defect whose traceback happens to contain ambiguous model-y language is
    still routed to the patch path. MV only fires when NO structural signal
    matched at all. Default is PS (a code defect is the common case — cheaper
    to attempt a patch than to refuse it).
    """
    text = str(error_text or "").lower()
    if any(t in text for t in FIX_PS_TERMS):
        return "prompt_sensitivity"
    if any(t in text for t in FIX_MV_TERMS):
        return "model_variability"
    return "prompt_sensitivity"


def _should_attempt_llm_patch(error_text: str) -> bool:
    """2026 L2 gate: skip the expensive T2 LLM repair for model_variability
    failures — the model is the limitation, so an LLM-generated patch cannot
    succeed and would only burn a /generate call + tokens."""
    return classify_fix_class(error_text) != "model_variability"

def load_cures() -> Dict[str, List[Dict]]:
    if CURES_FILE.exists():
        try:
            return json.loads(CURES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_cures(cures: Dict[str, List[Dict]]):
    KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
    CURES_FILE.write_text(json.dumps(cures, indent=2), encoding="utf-8")

def load_lessons() -> List[Dict]:
    if not LESSONS_FILE.exists():
        return []
    lessons = []
    with open(LESSONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                lessons.append(json.loads(line))
            except Exception:
                pass
    return lessons

def append_lesson(lesson: Dict):
    KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LESSONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(lesson) + "\n")

def validate_file(file_path: Optional[Path]) -> Tuple[bool, str]:
    if not file_path or not file_path.exists():
        return False, "no file to validate"
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        compile(content, file_path.name, "exec")
        return True, "ok"
    except SyntaxError as e:
        return False, f"syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"compile error: {e}"


def meta_classify_lessons(lessons: List[Dict]) -> Dict[str, Any]:
    from collections import Counter as _Counter
    if not lessons:
        return {"new_patterns": [], "merged_types": [], "orphan_errors": []}

    type_counts = _Counter(l.get("failure_type", "unknown") for l in lessons)
    success_rates = {}
    for ftype in type_counts:
        type_lessons = [l for l in lessons if l.get("failure_type") == ftype]
        successes = sum(1 for l in type_lessons if l.get("success"))
        success_rates[ftype] = successes / len(type_lessons) if type_lessons else 0

    low_success = [t for t, r in success_rates.items() if r < 0.3]
    merged = _Counter()
    for l in lessons:
        et = l.get("error_text", "").lower()
        for token in ["timeout", "connection", "http", "network"]:
            if token in et:
                merged["api_network_failure"] += 1
                break
        for token in ["import", "module", "package"]:
            if token in et:
                merged["import_dependency"] += 1
                break

    unrecognized = _Counter()
    for l in lessons:
        if l.get("failure_type") == "unknown":
            et = l.get("error_text", "").lower()
            for token in et.split()[:20]:
                if len(token) > 5:
                    unrecognized[token] += 1
    top_unknown_tokens = [t for t, _ in unrecognized.most_common(5)]

    return {
        "type_counts": dict(type_counts.most_common()),
        "success_rates": success_rates,
        "low_success_types": low_success,
        "suggested_merges": {t: c for t, c in merged.most_common(3) if c >= 2},
        "orphan_error_tokens": top_unknown_tokens,
    }


def generate_adversarial_test() -> List[str]:
    return [
        "def foo():\n    x = 1\n    if x = 1:\n        pass",
        "import non_existent_module_xyz",
        "data = {'key': 'value'}\nprint(data['missing'])",
        "for i in 42:\n    print(i)",
        "def bar():\n    return 1 +\n",
    ]


def run_adversarial_check(engine: Any) -> Dict[str, Any]:
    tests = generate_adversarial_test()
    results = {"total": 0, "detected": 0, "fixed": 0, "details": []}
    for code in tests:
        results["total"] += 1
        try:
            compile(code, "<adversarial>", "exec")
            results["details"].append({"code": code[:60], "detected": False, "fixed": False})
            continue
        except SyntaxError as e:
            error_text = f"SyntaxError: {e.msg} at line {e.lineno}"
        except Exception as e:
            error_text = f"{type(e).__name__}: {e}"

        ftype, tier = classify_failure(error_text)
        detected = ftype != "unknown"
        if detected:
            results["detected"] += 1
        repair = engine.diagnose_and_repair(error_text) if engine else {}
        results["details"].append({
            "code": code[:60],
            "detected": detected,
            "tier": tier,
            "fixed": repair.get("fixed", False),
            "failure_type": ftype,
        })
        if repair.get("fixed"):
            results["fixed"] += 1
    return results


def get_similar_lessons(error_text: str, top_k: int = 3) -> List[Dict]:
    lessons = load_lessons()
    # CLOSED LOOP (SOTA): merge LLM-distilled ReflexionMemory rules with the
    # static lesson knowledge base, so repairs are seeded by LEARNED corrections
    # from past failures — not just the hand-curated lessons file. Reflexion rules
    # are converted to the same lesson shape (error_keywords from the failure
    # reason, repair_action from the correction) and deduped against what's there.
    try:
        from swarm_os.services.reflection_loop import get_reflection_service
        import asyncio as _ai

        def _pull_reflexion_lesson():
            try:
                svc = get_reflection_service()
                # check_for_past_mistakes returns a [PAST-MISTAKE WARNING] string
                # built from the top retrieved ReflexionMemory rules for this error.
                warning = _ai.run(svc.check_for_past_mistakes(error_text, threshold=0.3, max_chars=1200))
                if warning and "PAST-MISTAKE" in warning:
                    lesson = {
                        "error_text": error_text[:200],
                        "repair_action": warning[:400],
                        "failure_type": "reflexion",
                        "success": None,
                        "source": "reflexion",
                        "error_keywords": [w for w in error_text.replace(".", " ").split() if len(w) > 3][:8],
                    }
                    if not any(l.get("source") == "reflexion" for l in lessons):
                        lessons.append(lesson)
            except Exception as _re:
                log.debug("reflexion rule merge skipped: %s", _re)

        try:
            _ai.get_running_loop().run_in_executor(None, _pull_reflexion_lesson)
        except RuntimeError:
            _pull_reflexion_lesson()
    except Exception as _merr:
        log.debug("reflexion merge unavailable: %s", _merr)

    error_lower = error_text.lower()
    scored = []
    for lesson in lessons:
        score = 0
        for token in lesson.get("error_keywords", []):
            if token.lower() in error_lower:
                score += 1
        for token in error_lower.split()[:10]:
            if token in lesson.get("error_text", "").lower():
                score += 0.5
        if score > 0:
            scored.append((score, lesson))
    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:top_k]]


class T0PatternRepair:
    @staticmethod
    def try_repair(error_text: str, file_path: Optional[Path] = None) -> Optional[str]:
        error_lower = error_text.lower()
        if "importerror" in error_lower or "module not found" in error_lower or "no module named" in error_lower:
            m = re.search(r"(?:import error|no module named)\s*['\"]?([a-zA-Z0-9_.]+)['\"]?", error_lower)
            if m:
                module_name = m.group(1)
                if file_path and file_path.exists():
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    if module_name.split(".")[0] not in content:
                        return f"pip install {module_name.split('.')[0]}"
                return f"pip install {module_name.split('.')[0]}"
        if "syntax" in error_lower or "indentation" in error_lower or "indent" in error_lower:
            if file_path and file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    ast.parse(content)
                except SyntaxError as e:
                    if e.lineno and e.msg:
                        lines = content.splitlines()
                        if e.lineno <= len(lines):
                            line = lines[e.lineno - 1]
                            if "self." not in line and "cls." not in line:
                                if e.msg and ("unexpected indent" in e.msg.lower() or "expected an indented block" in e.msg.lower()):
                                    if "expected an indented block" in e.msg.lower():
                                        # Use 4 spaces of indentation
                                        fixed_line = "    " + line.strip()
                                    else:
                                        # Remove indentation
                                        fixed_line = line.strip()
                                    fixed = "\n".join(
                                        l if i + 1 != e.lineno else fixed_line
                                        for i, l in enumerate(lines)
                                    )
                                    file_path.write_text(fixed, encoding="utf-8")
                                    return f"T0: Fixed indentation at {file_path.name}:{e.lineno}"
        return None


class T1ConstrainedRepair:
    @staticmethod
    def try_repair(error_text: str, file_path: Optional[Path] = None, context: Optional[Dict] = None) -> Optional[str]:
        if not file_path or not file_path.exists():
            return None
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)
        except SyntaxError:
            return None

        error_lower = error_text.lower()

        if "typeerror" in error_lower:
            m = re.search(r"argument of type\s+['\"]?(\w+)['\"]?\s+is not iterable", error_lower)
            if m:
                target_type = m.group(1)
                for node in ast.walk(tree):
                    if isinstance(node, ast.For):
                        iter_name = ast.unparse(node.iter) if hasattr(ast, 'unparse') else ""
                        if iter_name:
                            new_content = content.replace(
                                f"for {ast.unparse(node.target)} in {iter_name}",
                                f"for {ast.unparse(node.target)} in ({iter_name} if isinstance({iter_name}, (list, tuple, set, dict)) else [{iter_name}])"
                            )
                            if new_content != content:
                                file_path.write_text(new_content, encoding="utf-8")
                                return f"T1: Wrapped non-iterable {target_type} in list at {file_path.name}"

        if "keyerror" in error_lower:
            m = re.search(r"keyerror:\s*['\"]?(\w+)['\"]?", error_lower)
            if m:
                missing_key = m.group(1)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Subscript):
                        try:
                            full = ast.unparse(node)
                            # Match the key in brackets with either quote style
                            pattern = rf"{re.escape(full.split('[')[0])}\[['\"]{re.escape(missing_key)}['\"]\]"
                            if re.search(pattern, content):
                                parent = ast.unparse(node.value) if hasattr(ast, 'unparse') else ""
                                if parent:
                                    new_access = f"{parent}.get('{missing_key}')"
                                    new_content = re.sub(pattern, new_access, content)
                                    if new_content != content:
                                        file_path.write_text(new_content, encoding="utf-8")
                                        return f"T1: Replaced subscript {full} with .get() at {file_path.name}"
                        except Exception:
                            pass
        return None


class T2DeepRepair:
    @staticmethod
    def build_prompt(error_text: str, failure_type: str, similar_lessons: List[Dict], context_hint: str = "") -> str:
        lessons_section = ""
        if similar_lessons:
            lessons_section = "\n## Past Lessons from Similar Repairs\n" + "\n".join(
                f"- Previous error: {str(l.get('error_text', '') or '')[:200]}\n  Fix: {str(l.get('repair_action', '') or '')[:200]}\n  Result: {'Success' if l.get('success') else 'Failed'}"
                for l in similar_lessons
            )

        return (
            f"You are a diagnostic repair agent. Classify and fix the following error.\n\n"
            f"## Failure Classification\n"
            f"Error: {error_text[:1000]}\n"
            f"Classified Type: {failure_type}\n"
            f"{lessons_section}\n"
            f"{context_hint}\n\n"
            f"## Required Output Format\n"
            f"Return a JSON object with these keys:\n"
            f'- "root_cause": brief explanation of what caused the error\n'
            f'- "fix_strategy": how to fix it\n'
            f'- "files_to_modify": list of file paths to change\n'
            f'- "code_patch": the exact Python code diff or replacement\n'
            f'- "confidence": float 0.0-1.0\n'
            f'- "test_code": a Python test that would have caught this bug (or "" if not applicable)\n'
            f'- "test_patch": alias for test_code\n\n'
            f"Return ONLY valid JSON, no other text."
        )


BUDGET_TRACKER: Dict[str, Dict[str, float]] = {}
BUDGET_FILE = KNOWLEDGE_BASE_DIR / "budget_tracker.json"


def load_budget() -> Dict[str, Dict[str, float]]:
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_budget(budget: Dict[str, Dict[str, float]]):
    KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(budget, indent=2), encoding="utf-8")


def _optimize_tier_order(failure_type: str) -> List[int]:
    budget = load_budget()
    ft_data = budget.get(failure_type, {})
    if not ft_data:
        return [0, 1, 2]
    tiers = []
    for t in [0, 1, 2]:
        cost = ft_data.get(f"t{t}_avg_cost", 999)
        success = ft_data.get(f"t{t}_success_rate", 0)
        tiers.append((t, success / max(cost, 1)))
    tiers.sort(key=lambda x: -x[1])
    return [t for t, _ in tiers]


class TieredRepairOrchestrator:
    def __init__(self, cmd_ctx=None):
        self.cmd_ctx = cmd_ctx
        self.total_tokens = 0
        self.start_time = datetime.now(timezone.utc)

    def _snapshot_and_validate(self, file_path: Optional[Path], result: Dict[str, Any], original: str) -> bool:
        """Fail-closed acceptance gate: revert the file unless it survives ALL checks:
        path allowlist, anti-truncation, compile, and the module's own tests."""
        if not file_path or not file_path.exists():
            return result.get("fixed", False)

        # Constitutional path guard — never auto-repair tests/config/secrets/own source.
        if not _is_repairable_path(file_path):
            file_path.write_text(original, encoding="utf-8")
            result["fixed"] = False
            result["validation_error"] = "path not in allowlist / blocked sensitive path"
            if self.cmd_ctx:
                self.cmd_ctx.console.print(f"[red]Blocked repair on protected path: {file_path}[/red]")
            return False

        content = file_path.read_text(encoding="utf-8", errors="ignore")

        # Anti-truncation guard (Zeltrex L4): reject rewrites that shrank the file.
        if not _anti_truncation_ok(original, content):
            file_path.write_text(original, encoding="utf-8")
            result["fixed"] = False
            result["validation_error"] = "anti-truncation guard: rewrite shrank file by >20%"
            if self.cmd_ctx:
                self.cmd_ctx.console.print(f"[red]Anti-truncation guard triggered for {file_path} — reverted[/red]")
            return False

        # Compile gate (R12: no new syntax errors).
        valid, msg = validate_file(file_path)
        if not valid:
            file_path.write_text(original, encoding="utf-8")
            result["fixed"] = False
            result["validation_error"] = msg
            if self.cmd_ctx:
                self.cmd_ctx.console.print(f"[red]Validation failed, reverted: {msg}[/red]")
            return False

        # Quality gate (R11): run the repaired module's own tests before accepting.
        test_res = _run_related_tests(file_path)
        if test_res is not None and not test_res[0]:
            file_path.write_text(original, encoding="utf-8")
            result["fixed"] = False
            result["validation_error"] = f"related tests failed: {test_res[1][-500:]}"
            if self.cmd_ctx:
                self.cmd_ctx.console.print(f"[red]Related tests failed, reverted: {test_res[1][-200:]}[/red]")
            return False

        return result.get("fixed", False)

    def repair(self, error_text: str, file_path: Optional[Path] = None, context: Optional[Dict] = None) -> Dict[str, Any]:
        failure_type, tier = classify_failure(error_text)
        similar_lessons = get_similar_lessons(error_text)
        
        original_content = file_path.read_text(encoding="utf-8", errors="ignore") if file_path and file_path.exists() else ""

        # R18: fail closed when the circuit breaker is open or the daily cap is hit.
        breaker_allowed, breaker_reason = _circuit_allows_repair()
        if not breaker_allowed:
            result = {
                "error": error_text[:500],
                "failure_type": failure_type,
                "tier_used": None,
                "fixed": False,
                "repair_action": None,
                "root_cause": None,
                "confidence": 0.0,
                "similar_lessons_used": len(similar_lessons),
                "validation_error": breaker_reason,
                "generated_test": None,
                "skipped": True,
                "fix_class": classify_fix_class(error_text),
                "retry_dispatched": False,
            }
            if self.cmd_ctx:
                self.cmd_ctx.console.print(f"[yellow]⚕ {breaker_reason} — skipping repair.[/yellow]")
            return result

        result = {
            "error": error_text[:500],
            "failure_type": failure_type,
            "tier_used": tier,
            "fixed": False,
            "repair_action": None,
            "root_cause": None,
            "confidence": 0.0,
            "similar_lessons_used": len(similar_lessons),
            "validation_error": None,
            "generated_test": None,
            "fix_class": classify_fix_class(error_text),
            # 2026 L2 disclosure: this orchestrator only skips the LLM patch for
            # model_variability — it does NOT re-dispatch a generation attempt
            # here. Regeneration is the agent loop's job upstream. Set True only
            # if a retry is actually dispatched on this path.
            "retry_dispatched": False,
        }
        cures = load_cures()
        if failure_type in cures:
            for cure in cures[failure_type]:
                if any(kw in error_text.lower() for kw in cure.get("keywords", [])):
                    result["repair_action"] = cure["action"]
                    result["confidence"] = cure.get("confidence", 0.5)
                    if self.cmd_ctx:
                        self.cmd_ctx.console.print(f"[dim]Found cure for [{failure_type}]: {str(cure.get('action', '') or '')[:80]}...[/dim]")

        tier_order = _optimize_tier_order(failure_type)

        for attempt_tier in tier_order:
            if result["fixed"]:
                break
            if attempt_tier == 0:
                t0_result = T0PatternRepair.try_repair(error_text, file_path)
                if t0_result:
                    result["repair_action"] = t0_result
                    result["tier_used"] = 0
                    result["fixed"] = True
                    result["confidence"] = 0.8
                    self._snapshot_and_validate(file_path, result, original_content)
            elif attempt_tier == 1:
                t1_result = T1ConstrainedRepair.try_repair(error_text, file_path, context)
                if t1_result:
                    result["repair_action"] = t1_result
                    result["tier_used"] = 1
                    result["fixed"] = True
                    result["confidence"] = 0.6
                    self._snapshot_and_validate(file_path, result, original_content)
            elif attempt_tier == 2 and self.cmd_ctx:
                # 2026 L2 (diagnose-before-patch): a model_variability failure means
                # the model itself is the limitation — an LLM patch cannot succeed,
                # so skip the /generate call instead of burning tokens on retry.
                # Checked BEFORE the path guard: MV skips regardless of path.
                if not _should_attempt_llm_patch(error_text):
                    result["validation_error"] = "fix_class=model_variability: LLM patch skipped (model limitation, not patchable)"
                    result["fix_class"] = "model_variability"
                    if self.cmd_ctx:
                        self.cmd_ctx.console.print("[yellow]Skpping T2 LLM patch: model_variability failure (diagnose-before-patch)[/yellow]")
                    break
                # R6: don't burn an LLM call repairing a protected path.
                if file_path and not _is_repairable_path(file_path):
                    result["validation_error"] = "path not in allowlist / blocked sensitive path"
                    if self.cmd_ctx:
                        self.cmd_ctx.console.print(f"[red]Blocked T2 repair on protected path: {file_path}[/red]")
                    break
                from organism_console.api_client import call_api
                prompt = T2DeepRepair.build_prompt(error_text, failure_type, similar_lessons)
                try:
                    resp = call_api("/generate", "POST", {
                        "prompt": prompt,
                        "agent_id": getattr(self.cmd_ctx.state, "active_agent", "coder"),
                    })
                    if resp and resp.status_code == 200:
                        response_text = resp.json().get("response", "")
                        if "```json" in response_text:
                            m = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
                            if m:
                                response_text = m.group(1)
                        elif "```" in response_text:
                            m = re.search(r"```\s*(\{.*?\})\s*```", response_text, re.DOTALL)
                            if m:
                                response_text = m.group(1)
                        try:
                            diag = json.loads(response_text)
                            result["root_cause"] = diag.get("root_cause")
                            result["repair_action"] = diag.get("fix_strategy") or diag.get("code_patch")
                            result["confidence"] = float(diag.get("confidence", 0.3))
                            result["tier_used"] = 2
                            if diag.get("files_to_modify") or diag.get("code_patch"):
                                if diag.get("code_patch") and (not file_path or not file_path.exists()):
                                    result["validation_error"] = "LLM provided a code patch but no valid target file path was supplied"
                                    result["fixed"] = False
                                else:
                                    if diag.get("code_patch"):
                                        file_path.write_text(diag.get("code_patch"), encoding="utf-8")
                                    result["fixed"] = True
                                    self._snapshot_and_validate(file_path, result, original_content)
                            result["generated_test"] = diag.get("test_patch") or diag.get("test_code")
                        except (json.JSONDecodeError, ValueError):
                            result["repair_action"] = response_text[:500]
                    self.total_tokens += len(response_text) // 4 if response_text else 0
                except Exception as e:
                    log.error(f"T2 repair call failed: {e}")

        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        result["elapsed_seconds"] = round(elapsed, 2)
        result["tokens_used"] = self.total_tokens

        budget = load_budget()
        ft_data = budget.setdefault(failure_type, {})
        for t in [0, 1, 2]:
            key = f"t{t}_calls"
            ft_data[key] = ft_data.get(key, 0) + (1 if result.get("tier_used") == t else 0)
        for t in [0, 1, 2]:
            avg_key = f"t{t}_avg_cost"
            cnt_key = f"t{t}_calls"
            calls = ft_data.get(cnt_key, 0)
            if calls > 0:
                ft_data[avg_key] = (ft_data.get(avg_key, 0) * (calls - 1) + self.total_tokens) / calls
            sr_key = f"t{t}_success_rate"
            sr_calls = ft_data.get(cnt_key, 0)
            sr_successes = ft_data.get(f"t{t}_successes", 0) + (1 if result.get("fixed") and result.get("tier_used") == t else 0)
            ft_data[f"t{t}_successes"] = sr_successes
            ft_data[sr_key] = sr_successes / max(sr_calls, 1)
        save_budget(budget)

        append_lesson({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_text": error_text[:500],
            "error_keywords": [w for w in error_text.lower().split() if len(w) > 4][:20],
            "failure_type": failure_type,
            "tier_used": tier,
            "repair_action": result["repair_action"],
            "success": result["fixed"],
            "confidence": result["confidence"],
        })

        if not result.get("skipped"):
            _record_repair_result(result["fixed"])

        return result


class RepairWatchman:
    def __init__(self, engine: Any, interval_seconds: int = 30):
        self.engine = engine
        self.interval = interval_seconds
        self._running = False
        self._thread = None
        self._last_position = 0

    def start(self, start_at_end: bool = True):
        if self._running:
            return
        if start_at_end:
            try:
                event_file = KNOWLEDGE_BASE_DIR.parent.parent / "data" / "events" / "events.jsonl"
                if event_file.exists():
                    self._last_position = event_file.stat().st_size
            except Exception:
                pass
        self._running = True
        import threading
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _watch(self):
        event_file = KNOWLEDGE_BASE_DIR.parent.parent / "data" / "events" / "events.jsonl"
        if not event_file.exists():
            self._running = False
            return
        while self._running:
            try:
                current_size = event_file.stat().st_size
                if current_size > self._last_position:
                    with open(event_file, "r", encoding="utf-8") as f:
                        f.seek(self._last_position)
                        for line in f:
                            if not self._running:
                                return
                            try:
                                data = json.loads(line)
                                _handle_event_line(self.engine, data)
                            except Exception:
                                log.exception("Failed to parse event line at offset %d", self._last_position)
                                pass
                        self._last_position = f.tell()
            except Exception as e:
                log.warning(f"RepairWatchman iteration failed: {e}")

            import time as _time
            _time.sleep(self.interval)


def _handle_event_line(engine: Any, data: dict) -> None:
    """Handle ONE parsed event line from events.jsonl.

    Extracted from RepairWatchman._watch so the bug-prone parse logic (which
    crashed twice with 'NoneType' object is not subscriptable on null payloads)
    is directly unit-testable in isolation. All accesses are None-tolerant.
    Never raises: unexpected shapes are logged and skipped.
    """
    if not isinstance(data, dict):
        return
    if data.get("event_type") == "tool_result":
        res = (data.get("payload") or {}).get("result", {}) or {}
        if not res.get("ok", False):
            err = str(res.get("error", "") or "").strip()
            if err and len(err) < 500 and engine:
                import re
                from pathlib import Path
                payload = data.get("payload") or {}
                args = payload.get("arguments") or {}
                file_path_str = args.get("file_path") or args.get("TargetFile")
                if not file_path_str:
                    m = re.search(r'File "([^"]+\.py)"', err)
                    if m:
                        file_path_str = m.group(1)
                fpath = Path(file_path_str) if file_path_str else None
                if hasattr(engine, "diagnose_and_repair"):
                    engine.diagnose_and_repair(err, file_path=fpath)
                elif hasattr(engine, "repair"):
                    engine.repair(err, file_path=fpath)
    elif data.get("event_type") == "turn_budget_exhausted":
        # Turn-budget exhaustion is a LEARNING signal, not a code repair: a
        # compound goal (filesystem + web_search) burned its turns without
        # finishing. Record a reflexion so the next run gets a
        # [PAST-MISTAKE WARNING] to interleave / minimize tool calls — closing
        # the detection->correction loop for a failure class that used to leave
        # zero trace.
        try:
            payload = data.get("payload") or {}
            agent_id = payload.get("agent_id") or data.get("source") or "unknown"
            prompt = str(payload.get("prompt") or "")[:150]
            log.warning("turn_budget_exhausted for agent %s (prompt: %s)", agent_id, prompt)
            from swarm_os.services.reflection_loop import get_reflection_service
            import asyncio as _asyncio

            async def _record_turn_reflexion():
                await get_reflection_service().store_reflexion(
                    task=f"agent:{agent_id} compound goal {prompt} exhausted turns",
                    action="max_turns_reached",
                    failure_reason="agent ran out of turns before completing a compound goal.",
                    correction="Prefer completing the goal with the FEWEST tool calls. For compound goals needing both codebase reads and web research, interleave them — do not spend all turns on exploration.",
                    do_not_repeat=f"agent:{agent_id} must not burn all turns on exploration before the required tool.",
                    component=agent_id,
                    confidence=0.6,
                )

            try:
                _asyncio.get_running_loop().create_task(_record_turn_reflexion())
            except RuntimeError:
                _asyncio.run(_record_turn_reflexion())
        except Exception as tb_err:
            log.warning("Failed to record turn-budget reflexion: %s", tb_err)