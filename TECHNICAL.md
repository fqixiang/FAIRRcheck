# FAIRRcheck — Technical Reference

This document describes the internal architecture and implementation details of
`fairrcheck`: how metrics are detected, scored, sent to the LLM, and how patches
are generated. It is intended for developers extending or modifying the tool.

---

## Table of Contents

1. [Project Layout](#1-project-layout)
2. [Registry — Single Source of Truth](#2-registry--single-source-of-truth)
3. [Scanner Pipeline](#3-scanner-pipeline)
4. [Deterministic Detectors](#4-deterministic-detectors)
5. [Scoring Logic](#5-scoring-logic)
6. [LLM Client](#6-llm-client)
7. [LLM Metric Evaluation (`scan --llm`)](#7-llm-metric-evaluation-scan---llm)
8. [LLM Advice (`advise`)](#8-llm-advice-advise)
9. [Patch Agent (`fix`)](#9-patch-agent-fix)
10. [CLI Commands](#10-cli-commands)
11. [Reporters](#11-reporters)
12. [Known Limitations & Gaps](#12-known-limitations--gaps)

---

## 1. Project Layout

```
fairrcheck/
├── cli.py          — Typer CLI entry point (scan / advise / fix / info)
├── registry.py     — YAML registry loader; MetricSpec and Registry dataclasses
├── scanner.py      — Orchestrates per-metric evaluation; builds result dict
├── detectors.py    — Heuristic file-system detectors; DETECTOR_MAP dispatch table
├── scoring.py      — Per-principle averages and weighted overall FAIRR score
├── llm.py          — LLM HTTP client; prompts for evaluation, advice, patching
├── agent.py        — FixAgent; Aider subprocess or LLM diff generation
├── templates/      — Jinja2 HTML report template
└── reporters/      — json_reporter.py, html_reporter.py, pdf_reporter.py
config/
└── metrics_fairr_v1.yml  — The ONLY place where metrics are defined
examples/
├── unfairr_project/      — Grade F reference project
├── semifairr_project/    — Grade D reference project
└── fairrrish_project/    — Grade B reference project
```

---

## 2. Registry — Single Source of Truth

**File:** `config/metrics_fairr_v1.yml`  
**Loader:** `fairrcheck/registry.py`

The YAML defines every metric. Nothing in the Python code re-declares metric IDs
or names — the code only provides detection logic for metrics flagged
`implemented_in_prototype: true`.

### YAML structure

```yaml
schema_version: "1.0"
name: "FAIRR-HPC"
scoring:
  scale: [0, 1, 2]           # integer scoring scale; max_score = 2
  weights:
    F: 0.15
    A: 0.15
    I: 0.20
    R: 0.20
    R2: 0.30                  # Reproducibility is highest-weighted

metrics:
  - id: "FAIRR-F2"
    principle: "F"
    name: "Core descriptive metadata present"
    description: >
      Title, creators, description, date, version, and keywords present
      in README, CITATION.cff, codemeta, or metadata manifest.
    implemented_in_prototype: true
```

### Python dataclasses

```python
@dataclass
class MetricSpec:
    id: str
    principle: str
    name: str
    description: str           # used in LLM prompts
    implemented_in_prototype: bool

@dataclass
class Registry:
    schema_version: str
    name: str
    scale: List[int]
    weights: Dict[str, float]
    metrics: List[MetricSpec]
    # derived: _by_id, _by_principle lookup dicts
```

`registry.max_score` returns `max(scale)` — currently **2**.

---

## 3. Scanner Pipeline

**File:** `fairrcheck/scanner.py`

`run_scan(project_path, mode, use_llm, llm_config, registry, ...)` is the main
entry point. It:

1. Resolves and validates the project path.
2. Loads the registry (uses a pre-loaded `Registry` object if passed, otherwise
   reads from YAML — avoids double-loading when called from CLI).
3. Collects file excerpts (`collect_excerpts`) if `use_llm=True`.
4. Iterates over **all** 25 metrics in registry order.
5. For each metric, calls `_evaluate_metric(...)`.
6. Feeds all results into `compute_scores(...)`.
7. Returns a single structured result dict.

### File excerpt collection

```python
_INTERESTING_FILES = [
    "README.md", "README.rst", "README.txt", "README",
    "CITATION.cff", "codemeta.json", "metadata.json",
    "pyproject.toml", "requirements.txt", "environment.yml",
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "Dockerfile", "Singularity", "Apptainer",
]
_SENSITIVE_PATTERNS = [".env", "secrets", "credentials", "id_rsa", ".pem", ".key"]
```

Files larger than **100 KB** are skipped. Sensitive filenames are never read.

### `_evaluate_metric` logic

```
metric.implemented_in_prototype == False
  → return stub: {score: null, notes: "Not implemented in prototype"}

detector = DETECTOR_MAP.get(metric.id)  → None
  → return {score: 0, notes: "Detector missing"}

det = detector(project_path, mode)      → DetectorResult

if use_llm and det.score < max_score:
    llm_result = llm_evaluate_metric(...)
    final_score = max(det.score, llm_result["score_suggestion"])
    # LLM can only RAISE a score, never reduce it
```

### Result dict per metric

```json
{
  "metric_id": "FAIRR-F4",
  "name": "Machine-readable metadata",
  "principle": "F",
  "implemented": true,
  "score": 0,
  "max_score": 2,
  "evidence": ["pyproject.toml found"],
  "rationale": "Generic structured files found; no dedicated metadata descriptor.",
  "llm_used": false,
  "notes": ""
}
```

---

## 4. Deterministic Detectors

**File:** `fairrcheck/detectors.py`

Each detector is a function `(path: Path, mode: str) -> DetectorResult`.
The `mode` argument (`"development"` | `"publication"`) is available for
future stricter publication-mode checks; most detectors currently ignore it.

```python
@dataclass
class DetectorResult:
    score: int           # 0 | 1 | 2
    evidence: List[str]  # human-readable findings
    rationale: str       # one-line explanation
```

### Dispatch table

```python
DETECTOR_MAP = {
    "FAIRR-F2":   detect_FAIRR_F2,
    "FAIRR-F4":   detect_FAIRR_F4,
    "FAIRR-A1":   detect_FAIRR_A1,
    "FAIRR-I1":   detect_FAIRR_I1,   # reuses F4 logic
    "FAIRR-I3":   detect_FAIRR_I3,
    "FAIRR-R1.1": detect_FAIRR_R1_1,
    "FAIRR-R1.2": detect_FAIRR_R1_2,
    "FAIRR-R2.1": detect_FAIRR_R2_1,
    "FAIRR-R2.4": detect_FAIRR_R2_4,
    "FAIRR-R2.5": detect_FAIRR_R2_5,
}
```

### Detector details

| Metric | What is checked | Score 2 | Score 1 | Score 0 |
|--------|----------------|---------|---------|---------|
| **FAIRR-F2** | README / CITATION.cff / codemeta / metadata.json; keyword scan for title, description, creator | All 3 fields | 1–2 fields | No files or no fields |
| **FAIRR-F4** | `CITATION.cff`, `codemeta.json`, `metadata.json`, `ro-crate-metadata.json`, any `.yml`/`.json` | High-quality file (CITATION/codemeta/RO-Crate) | Generic structured file only | None found |
| **FAIRR-A1** | README keyword scan: `access`, `availability`, `restrict`, `license`, `embargo`, `open`, `public`, `private`, `confidential` | ≥3 keywords | 1–2 keywords | 0 keywords |
| **FAIRR-I1** | Identical to F4 (reused); framed as interoperability | Same as F4 | Same as F4 | Same as F4 |
| **FAIRR-I3** | Signals: input/data dir, container file, config/params YAML, output/results dir, commit ref in README, workflow file (Snakefile/Nextflow/WDL/GH Actions) | ≥4 signals | 2–3 signals | <2 signals |
| **FAIRR-R1.1** | LICENSE / LICENCE / COPYING file; SPDX identifier in README or CITATION.cff | File + SPDX | File or SPDX | Neither |
| **FAIRR-R1.2** | Signals: `.git` dir (weight 2), config snapshot, log files, version in CITATION/codemeta, provenance keyword in README | ≥4 signals | 2–3 signals | <2 signals |
| **FAIRR-R2.1** | `requirements*.txt`, `uv.lock`, `poetry.lock`, `Pipfile.lock`, `renv.lock`, `environment.yml`, `conda*.yml`, `Dockerfile`, `Singularity`, `pyproject.toml`, `setup.cfg` | Container or lockfile | pyproject.toml only | None |
| **FAIRR-R2.4** | `SHA256SUMS`, `checksums*.txt/sha256`, `md5sums`; sha256/checksum/hash mention in README or pyproject.toml/codemeta.json | Checksum file + documented | Either | Neither |
| **FAIRR-R2.5** | README section matching `reproduc\|how.to.run\|getting.started\|quickstart\|usage\|run\|install` (weight 2), code blocks in README, Makefile/run.sh/run.py/main.py/workflow scripts | ≥3 signals | 1–2 signals | 0 signals |

### Internal helpers

- `_find_readme(path)` — regex `^readme(\.(md|txt|rst))?$`, case-insensitive
- `_read_text_safe(p, max_bytes=50_000)` — reads at most 50 KB, UTF-8 with replace
- `_files_matching(path, *patterns)` — flat directory scan with regex patterns
- `_glob_exists(path, glob)` — recursive glob wrapper

---

## 5. Scoring Logic

**File:** `fairrcheck/scoring.py`

### Per-principle normalised score

Only metrics where `implemented=True` and `score is not None` are counted.

```
P_score = mean(score_i / max_score)  for all implemented metrics i in principle P
```

If a principle has zero implemented metrics with scores, its `normalised_score` is 0.

### Weighted overall FAIRR score

```
FAIRR = sum(weight_P * P_score) / sum(weight_P)  for P in {F, A, I, R, R2}
```

Weights: F=0.15, A=0.15, I=0.20, R=0.20, R2=0.30

### Letter grade thresholds

| Score | Grade |
|-------|-------|
| ≥ 0.85 | A |
| ≥ 0.70 | B |
| ≥ 0.50 | C |
| ≥ 0.30 | D |
| < 0.30 | F |

### Output structure

```json
{
  "principles": {
    "F": {"normalised_score": 0.5, "implemented_count": 2, "total_metrics": 7, "weight": 0.15},
    ...
  },
  "overall_fairr_score": 0.25,
  "grade": "F",
  "score_summary": {"F": 0.5, "A": 0.5, "I": 0.0, "R": 0.0, "R2": 0.333}
}
```

---

## 6. LLM Client

**File:** `fairrcheck/llm.py`

### Configuration (`LLMConfig`)

Reads from environment variables; all can be overridden programmatically.

| Env var | Default | Purpose |
|---------|---------|---------|
| `FAIRRCHECK_LLM_BASE_URL` | — | Full base URL, e.g. `https://willma.surf.nl/api/v0` |
| `FAIRRCHECK_LLM_MODEL` | — | Model identifier |
| `FAIRRCHECK_LLM_API_KEY` | — | API key value |
| `FAIRRCHECK_LLM_AUTH_HEADER` | `X-API-KEY` | Header name. Use `Authorization` for OpenAI Bearer |
| `FAIRRCHECK_LLM_COMPLETIONS_PATH` | `/chat/completions` | Appended to base URL |

Final URL: `{base_url}/{completions_path.lstrip('/')}`

For SURF Willma: `https://willma.surf.nl/api/v0/chat/completions`  
For OpenAI: set `base_url=https://api.openai.com`, `completions_path=/v1/chat/completions`, `auth_header=Authorization`

### HTTP transport

Uses **stdlib `urllib.request` only** — no `httpx`, `requests`, or other third-party
HTTP libraries. This is deliberate for HPC environments where additional
dependencies may be restricted.

Payload format:
```json
{
  "model": "<model>",
  "messages": [...],
  "temperature": 0.0,
  "max_tokens": 4096
}
```

Timeout: **120 seconds** per request.

### JSON extraction (`_extract_json`)

LLMs frequently misbehave by wrapping JSON in Markdown fences, adding leading
prose, or appending trailing notes. The extractor handles all cases:

1. **Strip opening fence** — removes ` ```json ` or ` ``` ` prefix
2. **Strip closing fence** — removes trailing ` ``` `
3. **Fast path** — `json.loads()` on the cleaned text
4. **Slow path** — `json.JSONDecoder.raw_decode()` scanned from the first `{` or
   `[`, which stops at the end of the first complete JSON value and ignores any
   trailing prose
5. **Raise** — if nothing parsed, raises `JSONDecodeError`

### Rescue for truncated suggestion arrays (`_rescue_suggestions`)

If `llm_advise` receives a response that was cut off mid-array (e.g. due to
`max_tokens` limits), the outer `{"suggestions": [...]}` wrapper may not close.
`_rescue_suggestions` scans the raw string and collects every complete JSON object
that contains a `"metric_id"` key, returning them as a list. This makes recovery
possible even from heavily truncated responses.

---

## 7. LLM Metric Evaluation (`scan --llm`)

**Function:** `llm_evaluate_metric` in `llm.py`  
**Triggered by:** `fairrcheck scan <path> --llm`

This is called for each implemented metric where the deterministic score is less
than `max_score` (i.e. there is room to improve). Metrics already at `max_score`
are skipped.

### Input to LLM

| Field | Content |
|-------|---------|
| `metric_id` | e.g. `FAIRR-F4` |
| `metric_name` | e.g. `Machine-readable metadata` |
| `metric_description` | Full text from YAML |
| `project_path` | Absolute path string |
| `file_excerpts` | All interesting files (each capped at 10,000 chars, total capped at 100,000 chars) |
| `deterministic_score` | Integer score from heuristic detector |
| `max_score` | 2 |

### System prompt

```
You are a FAIRR compliance evaluator for HPC/computational research projects.
You MUST respond with STRICT valid JSON only — no markdown, no prose.
Use the exact schema provided. Do not add extra fields.
```

### User prompt (template)

```
Evaluate this FAIRR metric for the project at path: {project_path}

Metric ID: {metric_id}
Metric name: {metric_name}
Metric description: {metric_description}

Scoring scale: 0–{max_score} (0=absent, 1=partial, 2=full)

Deterministic scanner result: {deterministic_score}/{max_score}

==== File excerpts ====
{excerpt_block}
=======================

Rules:
- If deterministic_score >= 1, do NOT return a lower score_suggestion.
- Focus on evidence in the file excerpts.
- Be conservative; only suggest score_suggestion=2 if clearly evidenced.

Respond with ONLY this JSON (no markdown fences):
{
  "metric_id": "<metric_id>",
  "score_suggestion": <integer 0–2>,
  "evidence_excerpt": "<brief quote or filename from excerpts>",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<one to three sentences>"
}
```

### Safety rule

The LLM score is applied to the scan result **only if** it is strictly greater
than the deterministic score — it can never reduce a score:

```python
final_score = max(det.score, llm_result["score_suggestion"])
```

### LLM parameters

| Parameter | Value |
|-----------|-------|
| `temperature` | 0.0 (deterministic) |
| `max_tokens` | 4096 |

---

## 8. LLM Advice (`advise`)

**Function:** `llm_advise` in `llm.py`  
**Triggered by:** `fairrcheck advise <path>`

### Pipeline

1. Run deterministic scan (no LLM) → `scan_result`
2. Filter low-scoring metrics: `score is not None AND score < max_score`
3. Load registry to get `description` per metric ID
4. Build `low_summary` JSON (up to 15 metrics)
5. Collect file excerpts: up to 5 files, each ≤3,000 chars, total ≤12,000 chars
6. Call LLM, parse response, rescue from truncation if needed

### Input to LLM

#### `low_summary` (per metric, up to 15 entries)

```json
[
  {
    "metric_id": "FAIRR-R1.1",
    "name": "License specified",
    "description": "Persistent licensing info in LICENSE file or SPDX identifier.",
    "score": 0,
    "max_score": 2,
    "evidence": [],
    "rationale": "No LICENSE file or SPDX identifier found."
  },
  ...
]
```

The `evidence` and `rationale` fields come directly from the detector run,
giving the LLM the detector's reasoning rather than just a bare score.
The `description` field comes from the YAML metric definition.

#### File excerpts

Up to 5 files from `_INTERESTING_FILES`, each truncated to 3,000 chars, total
capped at 12,000 chars:

```
### README.md
<content up to 3000 chars>

### pyproject.toml
<content up to 3000 chars>
...
```

### System prompt

```
You are a FAIRR compliance advisor. Respond with STRICT JSON only.
No markdown, no prose outside the JSON.
```

### User prompt (template)

```
Project path: {project_path}
Overall FAIRR score: {overall_fairr_score}

Low-scoring metrics:
{low_summary}

==== File excerpts ====
{excerpt_block}
=======================

Provide up to 8 actionable suggestions, prioritised by impact.
Keep each message under 30 words. Keep example_snippet under 10 lines.

Respond with ONLY this JSON:
{
  "suggestions": [
    {
      "metric_id": "<FAIRR-XX>",
      "priority": <1-5, 1=highest>,
      "message": "<clear actionable advice, max 30 words>",
      "example_snippet": "<short example, max 10 lines, or empty string>"
    }
  ]
}
```

### LLM parameters

| Parameter | Value |
|-----------|-------|
| `temperature` | 0.1 (slight variation for creativity) |
| `max_tokens` | 4096 |

### Response handling

1. `_extract_json` parses the response
2. If `parsed["suggestions"]` is non-empty → return directly
3. If response was truncated and `suggestions` wrapper is missing →
   `_rescue_suggestions` extracts individual suggestion objects
4. If nothing parsed → return `{"suggestions": [], "error": "...", "_raw": raw}`

---

## 9. Patch Agent (`fix`)

**File:** `fairrcheck/agent.py`  
**Triggered by:** `fairrcheck fix <path> [--apply]`

### Pipeline

```
Step 1: Deterministic scan            → scan_result
Step 2: llm_advise(scan_result)       → suggestions (up to 8, used top 5)
Step 3: FixAgent.generate(suggestions) → patches
Step 4: Display diffs; save to patches.json
Step 5: If --apply and user confirms  → apply each safe patch via `patch -p0`
```

### Allowed files (hardcoded)

The agent will only generate patches targeting these files:

```python
ALLOWED_FILES = {"README.md", "CITATION.cff", "metadata.json"}
```

### Patch generation strategy

For each suggestion, the agent tries in order:

1. **Aider subprocess** (if `aider --version` succeeds):
   ```
   aider --no-auto-commits --dry-run \
     --message "[FAIRR <metric_id>] <message>" \
     --file README.md --file CITATION.cff --file metadata.json
   ```
   Env vars `OPENAI_API_BASE`, `OPENAI_API_KEY`, `AIDER_MODEL` are set from
   `FAIRRCHECK_LLM_*` env vars for Aider compatibility.

2. **LLM diff fallback** (`llm_generate_patch`): picks the first existing
   allowed file (`README.md` → `CITATION.cff` → `metadata.json`), reads its
   content, and asks the LLM to produce a unified diff.

### LLM patch prompt (`llm_generate_patch`)

#### System prompt
```
You are a code patching assistant.
Output ONLY a valid unified diff (--- / +++ / @@ format).
No explanation, no markdown fences.
```

#### User prompt
```
File: {filename}
Improvement needed (metric {metric_id}): {message}

Current file content:
{file_content, truncated to 6,000 chars}

Produce a minimal unified diff that addresses the improvement.
The diff must be applicable with `patch -p0`.
```

| Parameter | Value |
|-----------|-------|
| `temperature` | 0.0 |
| `max_tokens` | 1000 |

### Patch validation

Before displaying or applying, `_validate_unified_diff` checks that the diff
only modifies files in `ALLOWED_FILES`. Patches that modify other filenames are
marked with a "Problem" and not applied even with `--apply`.

### Patch application

```python
subprocess.run(["patch", "-p0", "-i", patch_file], cwd=project_path)
```

Written to a temp file then cleaned up. Returns `True` on success.

---

## 10. CLI Commands

**File:** `fairrcheck/cli.py` — Typer + Rich

### `fairrcheck scan <path>`

Options: `--out`, `--mode development|publication`, `--llm`, `--registry`, `--verbose`

- Runs `run_scan`
- Shows Rich progress bar with `BarColumn`, `MofNCompleteColumn`, `TimeElapsedColumn`
- When `--llm`: second hidden task shows per-metric LLM progress
- Writes `report.json`, `report.html`, `report.pdf` (or `report_llm.*` with `--llm`)
- Prints summary panel + per-principle table

### `fairrcheck advise <path>`

Options: `--out`, `--registry`, `--verbose`

- Requires LLM env vars
- Loads registry once, passes to both `run_scan` and `llm_advise`
- Prints suggestions table sorted by priority
- Writes `advice.json`

### `fairrcheck fix <path>`

Options: `--out`, `--apply`, `--registry`, `--verbose`

- Requires LLM env vars
- 3-step pipeline: scan → advise → patch
- Dry-run by default; `--apply` triggers `patch -p0`
- Writes `patches.json`

### `fairrcheck info`

- Loads registry, prints full metric table with implementation status
- Shows principle weights and scale

---

## 11. Reporters

**Directory:** `fairrcheck/reporters/`

All reporters accept a `filename` keyword to support the `_llm` suffix.

| Reporter | Output | Notes |
|----------|--------|-------|
| `json_reporter.py` | `report.json` | `json.dumps` of full result dict |
| `html_reporter.py` | `report.html` | Jinja2 template at `templates/report.html.j2` |
| `pdf_reporter.py` | `report.pdf` | ReportLab; concise summary only |

The HTML template shows:
- Overall score + grade + LLM model (if used)
- Per-principle bar chart (CSS only)
- Per-metric table with evidence and rationale

---

## 12. Known Limitations & Gaps

### Detectors

- **15 of 25 metrics are not implemented** (`implemented_in_prototype: false`).
  These are scored `null` and excluded from principle averages entirely.
- **Mode is largely ignored** — detectors do not apply stricter rules for
  `publication` mode yet (no PID check, no schema validation, etc.)
- **FAIRR-I1 is identical to FAIRR-F4** — the same detection function is reused
  with a cosmetic rationale change.
- **All detectors are shallow (flat directory) by default** — only `_glob_exists`
  performs recursive traversal. Files buried in subdirectories are generally
  not found.

### LLM integration

- **`fix` only targets 3 files** (`README.md`, `CITATION.cff`, `metadata.json`).
  It cannot create new files (e.g. a `LICENSE` or `SHA256SUMS`), which limits
  usefulness for many low-scoring metrics.
- **No feedback loop** — `fix` does not re-scan after patching to verify that
  the score improved. It is a single-shot: scan → advise → diff → apply.
- **Patch quality depends entirely on LLM output** — there is no semantic
  validation that the generated diff actually addresses the metric.
- **`_rescue_suggestions` is a heuristic** — it works by scanning for any dict
  with a `"metric_id"` key, which could collect partial/invalid objects if the
  LLM output is sufficiently malformed.

### Scoring

- **Unimplemented metrics silently do not affect the score** — a principle with
  no implemented metrics scores 0%, not `null`. This can give misleadingly low
  scores for principles where real compliance exists but no detector is written.
- **Principles with zero implemented metrics get weight 0 effectively**
  (their `normalised_score` = 0 pulls down the weighted average).
