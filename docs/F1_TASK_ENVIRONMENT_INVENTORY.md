# F1 Task Environment Inventory (Experiment J)

Frozen record of the authorized F1 task environments. Captured 2026-10-02 from the
live venvs under `C:\Users\rober\Projects\swe_probe_work` (outside the repository
by design - `swe_rebench_probe.py` refuses an in-repo `SWE_PROBE_WORK`).

Each environment is an isolated venv on the interpreter its curriculum row
declares (`base_image_name = python_base_310` -> Python 3.10.11). The main
project `.venv` is untouched and nothing was installed globally.

## Target-workspace wiring

`run_repair_task.py:504` uses `WORK / <instance_id> / "repo"` as the target
workspace and `:315` REQUIRES that path to contain `.git`. The editable
install for each task must therefore resolve to that same tree. Verified:

| Task | Target workspace | Editable install resolves to | Verdict |
|---|---|---|---|
| `pallets__werkzeug-2583` | `repo` @ `1ce57f64` | `repo/src` | OK (remediated) |
| `pypa__twine-1066` | `repo` @ `4a1fc064` | `repo/twine` | OK |
| `pytest-dev__pyfakefs-916` | `repo` @ `95b2de37` | `repo` | OK |
| `pallets__click-2380` | `repo` @ `df9ad408` | `repo/src` | OK |

**Remediation record - `pallets__werkzeug-2583` only.** Its editable install had
been created from a stale duplicate tree (`.../work/repo`, no `.git`, 78 files
vs 50 in `repo/src/werkzeug`); `direct_url.json` and `__editable__*.pth` both
pointed there. Any edit the agent made to the real target workspace would therefore
not have been imported, and the declared fail-to-pass test could never pass. The
declared install procedure for this task is `pip install -q -e .` executed **from
the task repo**; re-running it offline (`--no-build-isolation --no-deps`) failed
because the build backend is not present in the venv, so the editable target was
repointed to `repo/src` and `direct_url.json` corrected. This is the same
mechanism the working `pallets__click-2380` environment already uses. Package
versions were NOT changed: `pip freeze` reports 21 packages before and after, and
the declared fail-to-pass test `tests/test_routing.py::test_part_isolating_default`
still fails pre-fix, so the task remains valid and unsolved.

## Task metadata

All four rows come from `qwen_train/curriculum/swe_pool.jsonl`; no task identity
was synthesized.

| instance_id | repo | base_commit | f2p | p2p |
|---|---|---|---|---|
| `pallets__werkzeug-2583` | pallets/werkzeug | 1ce57f64c9fecb655856dd1f3098c7bf7a1d37fa | 1 | 114 |
| `pypa__twine-1066` | pypa/twine | 4a1fc064a7899872ee845df6a8810bb51a6845ac | 3 | 19 |
| `pytest-dev__pyfakefs-916` | pytest-dev/pyfakefs | 95b2de37ae7ea2cde50b23dfd657938a4cf60d25 | 1 | 102 |
| `pallets__click-2380` | pallets/click | df9ad4085d60710b507b54c8fc369ee186eb1d64 | 2 | 23 |

Each checkout carries exactly one modified file - the declared fail-to-pass test
patch, which is the expected pre-fix task state.

## pip freeze per environment

### pallets__werkzeug-2583  (python Python 3.10.11)

    attrs==22.2.0
    cffi==1.15.1
    colorama==0.4.6
    cryptography==39.0.0
    ephemeral-port-reserve==1.1.4
    exceptiongroup==1.1.0
    greenlet==2.0.1
    iniconfig==1.1.1
    MarkupSafe==3.0.3
    packaging==22.0
    pluggy==1.0.0
    psutil==5.9.4
    py==1.11.0
    pycparser==2.21
    pytest==7.2.0
    pytest-timeout==2.1.0
    pytest-xprocess==0.22.2
    tomli==2.0.1
    watchdog==2.2.1
    # Editable Git install with no remote (Werkzeug==2.3.0.dev0)
    -e c:\users\rober\projects\swe_probe_work\pallets__werkzeug-2583\repo

### pypa__twine-1066  (python Python 3.10.11)

    backports.tarfile==1.2.0
    certifi==2026.7.22
    charset-normalizer==3.5.1
    colorama==0.4.6
    coverage==7.16.1
    docutils==0.23
    exceptiongroup==1.3.1
    idna==3.19
    importlib_metadata==9.0.1
    iniconfig==2.3.0
    jaraco.classes==3.4.0
    jaraco.context==6.1.2
    jaraco.functools==4.6.0
    keyring==25.7.0
    markdown-it-py==4.2.0
    mdurl==0.1.2
    more-itertools==11.1.0
    nh3==0.3.7
    packaging==26.3
    pkginfo==1.10.0
    pluggy==1.6.0
    pretend==1.0.9
    Pygments==2.21.0
    pytest==9.1.1
    pytest-socket==0.8.1
    pywin32-ctypes==0.2.3
    readme_renderer==46.0
    requests==2.34.2
    requests-toolbelt==1.0.0
    rfc3986==2.0.0
    rich==15.0.0
    tomli==2.4.1
    -e git+https://github.com/pypa/twine.git@4a1fc064a7899872ee845df6a8810bb51a6845ac#egg=twine
    typing_extensions==4.16.0
    urllib3==2.7.0
    zipp==4.1.0

### pytest-dev__pyfakefs-916  (python Python 3.10.11)

    colorama==0.4.6
    et_xmlfile==2.0.0
    exceptiongroup==1.3.1
    iniconfig==2.3.0
    numpy==1.26.4
    openpyxl==3.1.2
    packaging==26.3
    pandas==2.1.3
    pathlib2==2.3.7.post1
    pluggy==1.6.0
    -e git+https://github.com/pytest-dev/pyfakefs.git@95b2de37ae7ea2cde50b23dfd657938a4cf60d25#egg=pyfakefs
    Pygments==2.21.0
    pytest==9.1.1
    python-dateutil==2.9.0.post0
    pytz==2026.3.post1
    scandir==1.10.0
    six==1.17.0
    tomli==2.4.1
    typing_extensions==4.16.0
    tzdata==2026.4
    xlrd==2.0.1

### pallets__click-2380  (python Python 3.10.11)

    -e git+https://github.com/pallets/click.git@df9ad4085d60710b507b54c8fc369ee186eb1d64#egg=click
    colorama==0.4.6
    exceptiongroup==1.3.1
    iniconfig==2.3.0
    packaging==26.3
    pluggy==1.6.0
    Pygments==2.21.0
    pytest==9.1.1
    tomli==2.4.1
    typing_extensions==4.16.0


