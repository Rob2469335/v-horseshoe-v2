"""F2 model/artifact provenance: the conversion chain, recorded and checked.

Why this module exists
----------------------
``swarm_os.services.experiment_model_identity`` proves which GGUF bytes are being
SERVED and under which alias. That is necessary but not sufficient for
reproducibility, and it is not what the experiment record actually demands. The
governing document requires, as separate REQUIRED RECORDs, the LoRA identity and
its hash. Nothing in the repository verified any of it.

The missing links are the ones that make a model reproducible:

    base model  ->  adapter (LoRA)  ->  training corpus  ->  merged artifact  ->  served GGUF

Each arrow can be broken silently. A model can be served whose bytes nobody can
trace to a corpus, and the served-artifact check still passes. This module
records every link explicitly and fails closed on any that is unrecorded.

Hard rules this module obeys
----------------------------
* **Never reads training content.** Corpus facts come from filesystem metadata
  (existence, size, mtime) and from the training-run record's own declared
  ``data`` filename. No line of any corpus is opened.
* **Never turns a timestamp into proof.** Filesystem and run timestamps are
  recorded as *observations*, never as evidence that one artifact was derived
  from another. A link is ``PROVEN`` only when something recorded it.
* **Fails closed and names the exact missing artifact.** An unverifiable chain
  reports which link broke and what record would fix it, so the gap is actionable
  rather than a vague "provenance incomplete".
* **Distinguishes the dimensions.** Base identity, adapter identity, corpus
  identity, conversion identity, served-artifact identity and execution-time
  exposure are separate fields; satisfying one never implies another.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "LINK_BASE",
    "LINK_ADAPTER",
    "LINK_CORPUS",
    "LINK_CONVERSION",
    "LINK_SERVED",
    "ALL_LINKS",
    "LINK_REMEDY",
    "CONVERSION_REQUIRED_FIELDS",
    "CONVERSION_FIELD_REMEDY",
    "CONVERSION_BINDINGS",
    "validate_conversion_record",
    "derive_conversion_record",
    "ChainLink",
    "ConversionChain",
    "ProvenanceVerdict",
    "ProvenanceError",
    "file_digest",
    "read_training_runs",
    "build_conversion_chain",
    "verify_conversion_chain",
    "render_chain",
    "parse_chain",
]

SCHEMA = "f2_model_provenance_v1"

LINK_BASE = "base_model"
LINK_ADAPTER = "adapter"
LINK_CORPUS = "training_corpus"
LINK_CONVERSION = "conversion"
LINK_SERVED = "served_artifact"
ALL_LINKS: tuple[str, ...] = (
    LINK_BASE, LINK_ADAPTER, LINK_CORPUS, LINK_CONVERSION, LINK_SERVED,
)

#: What record would close each broken link. Surfaced verbatim in the verdict so
#: the operator is told exactly what is missing.
LINK_REMEDY: dict[str, str] = {
    LINK_BASE: "record the base model id and immutable snapshot revision",
    LINK_ADAPTER: "record the adapter directory, its config digest, rank and alpha",
    LINK_CORPUS: "record the training-run 'data' field naming the consumed corpus",
    LINK_CONVERSION: (
        "record an explicit adapter->merged-artifact->GGUF conversion step; a "
        "timestamp ordering is NOT a conversion record"
    ),
    LINK_SERVED: "record the served GGUF path and its SHA-256",
}

#: Every field a conversion record must carry. A record is only a *conversion*
#: record if it names the whole operation: naming a merged artifact says what
#: exists, not what produced it. Missing any of these leaves the link
#: UNRECORDED -- a free-text ``detail`` asserting a tool is not a field.
CONVERSION_REQUIRED_FIELDS: tuple[str, ...] = (
    "source_base",
    "source_base_sha256",
    "source_adapter",
    "source_adapter_sha256",
    "merge_operation",
    "conversion_tool",
    "conversion_tool_version",
    "conversion_inputs",
    "merged_artifact",
    "merged_artifact_sha256",
    "served_gguf",
    "served_gguf_sha256",
    "operator",
)

#: Conversion-record field -> what it must actually contain.
CONVERSION_FIELD_REMEDY: dict[str, str] = {
    "source_base": "identity of the base artifact that was adapted",
    "source_base_sha256": "SHA-256 of that base artifact",
    "source_adapter": "identity of the adapter that was merged",
    "source_adapter_sha256": "SHA-256 of that adapter's config/weights",
    "merge_operation": "identity of the merge operation (script + arguments)",
    "conversion_tool": "identity of the converter used",
    "conversion_tool_version": "version of that converter",
    "conversion_inputs": "ordered input identities the converter consumed",
    "merged_artifact": "identity of the merged/intermediate artifact",
    "merged_artifact_sha256": "SHA-256 of that merged artifact",
    "served_gguf": "identity of the GGUF actually served",
    "served_gguf_sha256": "SHA-256 of that GGUF",
    "operator": "operator or process identity, where governed",
}

#: Fields whose recorded digest must agree with the chain link that proves the
#: same artifact. This is what makes the record checkable rather than a
#: checklist: a record naming a *different* adapter than the chain proves is a
#: MISMATCH, not a pass.
CONVERSION_BINDINGS: tuple[tuple[str, str], ...] = (
    ("source_base_sha256", LINK_BASE),
    ("source_adapter_sha256", LINK_ADAPTER),
    ("served_gguf_sha256", LINK_SERVED),
)


def validate_conversion_record(record: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return the required conversion fields that are absent or blank.

    Fail-closed. ``CONVERSION_REQUIRED_FIELDS`` names the whole operation, so a
    record that supplies only a merged artifact or only prose stays
    UNRECORDED. Timestamps are never consulted here: they are observations, not
    conversion evidence.
    """
    if not record:
        return CONVERSION_REQUIRED_FIELDS
    missing: list[str] = []
    for name in CONVERSION_REQUIRED_FIELDS:
        value = record.get(name)
        if value is None:
            missing.append(name)
        elif isinstance(value, str) and not value.strip():
            missing.append(name)
        elif isinstance(value, (list, tuple, set, dict)) and len(value) == 0:
            missing.append(name)
    return tuple(missing)


def _artifact_digest_if_file(path: Path) -> str:
    """SHA-256 of a single-file artifact, or "" when it is absent or a directory.

    A directory of weights has no cheap whole-content digest, so this refuses to
    invent one rather than substituting a stand-in (for example a config file's
    digest) that would look like provenance it is not.
    """
    try:
        if path.is_file():
            return file_digest(path)[0]
    except OSError:
        return ""
    return ""


def derive_conversion_record(
    *,
    source_adapter: str | Path | None = None,
    merged_artifact: str | Path | None = None,
    served_gguf: str | Path | None = None,
    conversion_tool: str | Path | None = None,
    conversion_tool_version: str = "",
    merge_operation: str = "",
    operator: str = "",
    conversion_inputs: Iterable[str] = (),
    source_base: str | Path | None = None,
    source_base_sha256: str = "",
) -> dict[str, Any]:
    """Derive a conversion record from artifacts actually present on disk.

    Every value emitted here is READ FROM A REAL ARTIFACT. A field that cannot be
    observed is **omitted**, never guessed, so the conversion link stays
    UNRECORDED through :func:`validate_conversion_record`. This is what makes the
    legitimate remediation route -- re-perform the merge and conversion under a
    witnessed operation -- machine-checkable instead of hand-typed.

    Facts about the OPERATION (converter version, the merge command, who ran it,
    the input order) are **not derivable from files**. Whoever witnesses the
    operation must supply them. Supplying them is a human attestation; this
    function only assembles and never originates them.

    It cannot be used to recover the historical record for an artifact whose
    merged weights are gone: ``merged_artifact`` is omitted when that path does
    not exist, which leaves the link UNRECORDED.
    """
    record: dict[str, Any] = {}

    if source_adapter:
        adir = Path(source_adapter)
        cfg = adir / "adapter_config.json"
        if cfg.is_file():
            record["source_adapter"] = adir.name
            try:
                record["source_adapter_sha256"] = file_digest(cfg)[0]
            except OSError:
                pass
            facts = _adapter_facts(adir)
            declared = str(facts.get("base_model_name_or_path") or "").strip()
            if declared:
                record["source_base"] = Path(declared).name

    if source_base and not record.get("source_base"):
        bpath = Path(source_base)
        record["source_base"] = bpath.name
        derived = _artifact_digest_if_file(bpath)
        if derived:
            record["source_base_sha256"] = derived
    if source_base_sha256:
        record["source_base_sha256"] = source_base_sha256

    if merged_artifact:
        mpath = Path(merged_artifact)
        if mpath.exists():
            record["merged_artifact"] = mpath.name
            derived = _artifact_digest_if_file(mpath)
            if derived:
                record["merged_artifact_sha256"] = derived

    if served_gguf:
        gpath = Path(served_gguf)
        if gpath.is_file():
            record["served_gguf"] = str(gpath)
            try:
                record["served_gguf_sha256"] = file_digest(gpath)[0]
            except OSError:
                pass

    if conversion_tool:
        record["conversion_tool"] = Path(conversion_tool).name
    if str(conversion_tool_version).strip():
        record["conversion_tool_version"] = str(conversion_tool_version).strip()
    if str(merge_operation).strip():
        record["merge_operation"] = str(merge_operation).strip()
    if str(operator).strip():
        record["operator"] = str(operator).strip()
    inputs = [str(i).strip() for i in conversion_inputs if str(i).strip()]
    if inputs:
        record["conversion_inputs"] = inputs

    return record


class ProvenanceError(RuntimeError):
    """Raised when provenance cannot be established. Never a pass."""


@dataclass(frozen=True)
class ChainLink:
    """One link in the conversion chain."""

    name: str
    state: str  # "PROVEN" | "UNRECORDED" | "MISMATCH"
    identity: str = ""
    digest: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "identity": self.identity,
            "digest": self.digest,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ConversionChain:
    """The recorded base -> adapter -> corpus -> conversion -> served chain."""

    schema: str
    model_alias: str
    links: tuple[ChainLink, ...]
    observations: tuple[tuple[str, str], ...] = ()
    notes: str = ""

    def link(self, name: str) -> ChainLink | None:
        for l in self.links:
            if l.name == name:
                return l
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "model_alias": self.model_alias,
            "links": [l.to_dict() for l in self.links],
            "observations": [
                {"fact": k, "value": v} for k, v in self.observations
            ],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ProvenanceVerdict:
    satisfied: bool
    proven_links: tuple[str, ...]
    unrecorded_links: tuple[str, ...]
    mismatched_links: tuple[str, ...]
    detail: str
    remedies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "proven_links": list(self.proven_links),
            "unrecorded_links": list(self.unrecorded_links),
            "mismatched_links": list(self.mismatched_links),
            "detail": self.detail,
            "remedies": list(self.remedies),
        }


def file_digest(path: str | Path, *, chunk: int = 8 * 1024 * 1024) -> tuple[str, int]:
    """SHA-256 and byte size of a file. Metadata + content; never executes it."""
    p = Path(path)
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def read_training_runs(path: str | Path) -> list[dict[str, Any]]:
    """Read the training-run ledger.

    This ledger records METADATA about runs (which adapter, which corpus filename,
    rank, alpha, timestamp). It is not training content, and nothing here reads a
    corpus file.
    """
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(row, Mapping):
            out.append(dict(row))
    return out


def _adapter_facts(adapter_dir: str | Path) -> dict[str, Any]:
    """Read an adapter's CONFIG only. Weights are never opened."""
    d = Path(adapter_dir)
    cfg = d / "adapter_config.json"
    facts: dict[str, Any] = {"dir": str(d), "config_present": cfg.is_file()}
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            facts["config_error"] = str(exc)
            return facts
        facts["base_model_name_or_path"] = str(data.get("base_model_name_or_path") or "")
        facts["peft_type"] = str(data.get("peft_type") or "")
        facts["r"] = data.get("r")
        facts["lora_alpha"] = data.get("lora_alpha")
        digest, size = file_digest(cfg)
        facts["config_sha256"] = digest
        facts["config_bytes"] = size
    return facts


def build_conversion_chain(
    *,
    model_alias: str,
    training_runs_path: str | Path,
    adapter_dirs: Iterable[str | Path] = (),
    corpus_search_roots: Iterable[str | Path] = (),
    served_gguf: str | Path | None = None,
    served_gguf_sha256: str = "",
    conversion_record: Mapping[str, Any] | None = None,
    notes: str = "",
) -> ConversionChain:
    """Assemble the chain from metadata that already exists on disk.

    ``conversion_record`` is the only input that can close the conversion link,
    because nothing on disk records that the served GGUF was produced from a
    given adapter. Timestamps are captured as observations and are deliberately
    NOT used to infer it.
    """
    observations: list[tuple[str, str]] = []
    runs = read_training_runs(training_runs_path)
    observations.append(("training_run_records", str(len(runs))))

    # --- base model: whatever an adapter config declares ---
    base_id, base_digest, base_state, base_detail = "", "", "UNRECORDED", ""
    for adir in adapter_dirs:
        f = _adapter_facts(adir)
        if f.get("base_model_name_or_path"):
            base_id = Path(str(f["base_model_name_or_path"])).name
            observations.append(("adapter_base_path", str(f["base_model_name_or_path"])))
            base_state = "PROVEN"
            base_detail = "declared by adapter_config.json base_model_name_or_path"
            # The immutable snapshot is the part that makes it reproducible.
            snap = str(f["base_model_name_or_path"]).rstrip("/").split("/")[-1]
            if len(snap) >= 7:
                base_digest = snap
                observations.append(("base_snapshot", snap))
            break
    if base_state == "UNRECORDED":
        base_detail = "no adapter config declares a base model"

    # --- adapter identity ---
    adapter_id, adapter_digest, adapter_state, adapter_detail = "", "", "UNRECORDED", ""
    for adir in adapter_dirs:
        f = _adapter_facts(adir)
        if f.get("config_present"):
            adapter_id = Path(str(adir)).name
            adapter_digest = str(f.get("config_sha256") or "")
            adapter_state = "PROVEN"
            adapter_detail = (
                f"adapter_config.json r={f.get('r')} alpha={f.get('lora_alpha')} "
                f"peft={f.get('peft_type')}"
            )
            observations.append(("adapter_r", str(f.get("r"))))
            observations.append(("adapter_alpha", str(f.get("lora_alpha"))))
            observations.append(("adapter_weights_bytes",
                                 str(_dir_size(Path(str(adir))))))
            break
    if adapter_state == "UNRECORDED":
        adapter_detail = "no adapter_config.json found"

    # --- training corpus: the run ledger's own declared 'data' filename ---
    corpus_name, corpus_digest, corpus_state, corpus_detail = "", "", "UNRECORDED", ""
    for run in runs:
        data = str(run.get("data") or "").strip()
        if not data:
            continue
        corpus_name = Path(data).name
        for root in corpus_search_roots:
            cand = Path(root) / corpus_name
            if cand.is_file():
                size = cand.stat().st_size
                corpus_state = "PROVEN"
                corpus_detail = (
                    f"declared by training_runs.jsonl 'data'; present at {cand} "
                    f"({size} bytes, content not read)"
                )
                observations.append(("corpus_bytes", str(size)))
                observations.append(("corpus_mtime", _mtime(cand)))
                observations.append(("corpus_sha256_not_computed", "1"))
                break
        if corpus_state == "PROVEN":
            observations.append(("training_run_ts", str(run.get("ts") or "")))
            break
    if corpus_state == "UNRECORDED":
        corpus_detail = (
            "no training-run record names a consumed corpus file"
            if not runs else
            f"training-run record names {corpus_name!r} but it was not found in the "
            "searched roots"
        )

    # --- conversion: only a complete, chain-bound record closes this ---
    if conversion_record:
        conv_id = str(conversion_record.get("merged_artifact") or "")
        conv_digest = str(conversion_record.get("merged_artifact_sha256") or "")
        absent = validate_conversion_record(conversion_record)
        if absent:
            conv_state = "UNRECORDED"
            conv_detail = (
                f"conversion record is incomplete: {len(absent)} of "
                f"{len(CONVERSION_REQUIRED_FIELDS)} required field(s) absent "
                f"({', '.join(absent[:4])}"
                f"{', ...' if len(absent) > 4 else ''}). Naming a merged artifact "
                "says what exists, not what produced it."
            )
        else:
            conv_state = "PROVEN"
            conv_detail = (
                f"{conversion_record.get('conversion_tool')} "
                f"{conversion_record.get('conversion_tool_version')} via "
                f"{conversion_record.get('merge_operation')}"
            )
    else:
        conv_id, conv_digest = "", ""
        conv_state = "UNRECORDED"
        conv_detail = (
            "no record states which adapter produced the served GGUF; file and run "
            "timestamps are observations, NOT conversion evidence"
        )

    # --- served artifact ---
    if served_gguf:
        p = Path(served_gguf)
        if p.is_file():
            digest, size = file_digest(p)
            observations.append(("served_gguf_bytes", str(size)))
            observations.append(("served_gguf_mtime", _mtime(p)))
            if served_gguf_sha256:
                served_state = "PROVEN" if served_gguf_sha256 == digest else "MISMATCH"
                served_detail = (
                    "recorded SHA-256 matches the bytes on disk"
                    if served_state == "PROVEN"
                    else f"recorded {served_gguf_sha256} != actual {digest}"
                )
            else:
                served_state = "PROVEN"
                served_detail = "digest computed from the served bytes"
            served_id, served_dg = str(p), digest
        else:
            served_id, served_dg = str(p), ""
            served_state, served_detail = "UNRECORDED", "served artifact does not exist"
    else:
        served_id, served_dg = "", ""
        served_state, served_detail = "UNRECORDED", "no served artifact supplied"

    links = (
        ChainLink(LINK_BASE, base_state, base_id, base_digest, base_detail),
        ChainLink(LINK_ADAPTER, adapter_state, adapter_id, adapter_digest, adapter_detail),
        ChainLink(LINK_CORPUS, corpus_state, corpus_name, corpus_digest, corpus_detail),
        ChainLink(LINK_CONVERSION, conv_state, conv_id, conv_digest, conv_detail),
        ChainLink(LINK_SERVED, served_state, served_id, served_dg, served_detail),
    )

    # The record must bind to the SAME artifacts the rest of the chain proves.
    # Without this, a complete-looking record naming a different adapter than
    # the one PROVEN above would close the link on its own authority.
    if conversion_record and conv_state == "PROVEN":
        by_name = {l.name: l for l in links}
        for field, link_name in CONVERSION_BINDINGS:
            claimed = str(conversion_record.get(field) or "").strip().lower()
            other = by_name[link_name]
            if not claimed or not other.digest or other.state != "PROVEN":
                continue
            if claimed != other.digest.strip().lower():
                links = tuple(
                    ChainLink(
                        l.name,
                        "MISMATCH",
                        l.identity,
                        l.digest,
                        f"conversion record {field}={claimed[:16]}... disagrees with "
                        f"the proven {link_name} digest {other.digest[:16]}...",
                    )
                    if l.name == LINK_CONVERSION
                    else l
                    for l in links
                )
                break

    return ConversionChain(
        schema=SCHEMA,
        model_alias=model_alias,
        links=links,
        observations=tuple(sorted(set(observations))),
        notes=notes,
    )


def _dir_size(p: Path) -> int:
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def _mtime(p: Path) -> str:
    import datetime as _dt

    try:
        return _dt.datetime.fromtimestamp(p.stat().st_mtime, _dt.timezone.utc).isoformat()
    except OSError:
        return ""


def verify_conversion_chain(chain: ConversionChain | Mapping[str, Any]) -> ProvenanceVerdict:
    """Recompute the provenance verdict. Fails closed, naming each broken link."""
    if isinstance(chain, Mapping):
        chain = parse_chain(chain)

    proven: list[str] = []
    unrecorded: list[str] = []
    mismatched: list[str] = []
    for name in ALL_LINKS:
        link = chain.link(name)
        if link is None:
            unrecorded.append(name)
        elif link.state == "PROVEN":
            proven.append(name)
        elif link.state == "MISMATCH":
            mismatched.append(name)
        else:
            unrecorded.append(name)

    ok = not unrecorded and not mismatched
    if ok:
        detail = (
            f"all {len(proven)} provenance links recorded and consistent for "
            f"alias {chain.model_alias!r}"
        )
        remedies: tuple[str, ...] = ()
    else:
        parts = []
        if unrecorded:
            parts.append(f"UNRECORDED: {unrecorded}")
        if mismatched:
            parts.append(f"MISMATCHED: {mismatched}")
        detail = "model provenance NOT established -- " + "; ".join(parts)
        remedies = tuple(
            LINK_REMEDY[n] for n in ALL_LINKS if n in unrecorded or n in mismatched
        )

    return ProvenanceVerdict(
        satisfied=ok,
        proven_links=tuple(proven),
        unrecorded_links=tuple(unrecorded),
        mismatched_links=tuple(mismatched),
        detail=detail,
        remedies=remedies,
    )


def render_chain(chain: ConversionChain) -> bytes:
    return json.dumps(chain.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_chain(data: Mapping[str, Any] | bytes) -> ConversionChain:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = json.loads(bytes(data).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ProvenanceError(f"chain is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ProvenanceError("chain must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise ProvenanceError(f"chain schema {data.get('schema')!r} != {SCHEMA!r}")
    try:
        links = tuple(
            ChainLink(
                name=str(l["name"]), state=str(l["state"]),
                identity=str(l.get("identity", "")),
                digest=str(l.get("digest", "")),
                detail=str(l.get("detail", "")),
            )
            for l in data.get("links", [])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvenanceError(f"chain link records are malformed: {exc}") from exc
    return ConversionChain(
        schema=SCHEMA,
        model_alias=str(data.get("model_alias", "")),
        links=links,
        observations=tuple(
            (str(o["fact"]), str(o["value"])) for o in data.get("observations", [])
            if isinstance(o, Mapping) and "fact" in o and "value" in o
        ),
        notes=str(data.get("notes", "")),
    )
