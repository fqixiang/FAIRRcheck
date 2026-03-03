# FAIRRcheck

**FAIRR compliance checker for HPC / computational research projects.**

Built as a solution prototype for [SURF](https://www.surf.nl/)'s HPC.

---

## FAIRR — What it is

FAIRR extends the FAIRsFAIR principles (Findable, Accessible, Interoperable, Reusable)
with an explicit **Reproducible (R2)** dimension, tailored for computational research on HPC.

The full metric registry lives in [`config/metrics_fairr_v1.yml`](config/metrics_fairr_v1.yml).

## Installation

```bash
# With uv (recommended)
uv sync

# Or with pip
pip install -e .
```

## Quick Start

```bash
# Show the loaded FAIRR registry
fairrcheck info

# Scan the "fairrrish" example project (deterministic)
fairrcheck scan examples/fairrrish_project --mode development

# Scan the "unfairr" example project
fairrcheck scan examples/unfairr_project

# Augment scan with LLM (requires SURF AI-Hub configuration)
export FAIRRCHECK_LLM_BASE_URL=https://willma.surf.nl/api/v0
export FAIRRCHECK_LLM_MODEL=openai/gpt-oss-120b
export FAIRRCHECK_LLM_API_KEY=YOUR_API_KEY_HERE
fairrcheck scan examples/fairrrish_project --llm

# LLM-powered improvement advice (shows prioritised suggestions table)
fairrcheck advise examples/semifairr_project

# Generate fix patches for failing metrics (dry-run, shows unified diffs)
fairrcheck fix examples/semifairr_project

# Generate and apply patches
fairrcheck fix examples/semifairr_project --apply
```

## Output

All reports are written to `<project_path>/fairrcheck_out/`:

| File | Description |
|------|-------------|
| `report.json` | Full structured JSON result |
| `report.html` | Visual HTML report with per-principle breakdown |
| `report.pdf` | Concise PDF summary (ReportLab) |
| `report_llm.json/html/pdf` | When `--llm` flag is used |
| `advice.json` | LLM advisory output (`advise` command) |
| `patches.json` | Generated diff patches (`fix` command) |

## Environment Variables (LLM / SURF AI-Hub)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FAIRRCHECK_LLM_BASE_URL` | Yes (for LLM) | `https://willma.surf.nl/api/v0` | Full base URL |
| `FAIRRCHECK_LLM_MODEL` | Yes (for LLM) | `openai/gpt-oss-120b` | Model name |
| `FAIRRCHECK_LLM_API_KEY` | Yes (for LLM) | — | API key value |
| `FAIRRCHECK_LLM_COMPLETIONS_PATH` | No | `/chat/completions` | Path appended to base URL for chat completions |

## Metric Registry

25 metrics across 5 principles. 10 are implemented in the prototype (✓); the rest are defined in the YAML and scored as `null` (—).

| ID | Principle | Name | Impl. |
|----|-----------|------|-------|
| `FAIRR-F1` | F | Unique identifier (internal or global) | — |
| `FAIRR-F1-P` | F | Persistent identifier or PID-readiness | — |
| `FAIRR-F1-V` | F | Versioning discipline | — |
| `FAIRR-F2` | F | Core descriptive metadata present | ✓ |
| `FAIRR-F3` | F | Metadata references identified object | — |
| `FAIRR-F4` | F | Machine-readable metadata | ✓ |
| `FAIRR-F5` | F | Internal discoverability | — |
| `FAIRR-A1` | A | Access conditions documented | ✓ |
| `FAIRR-A2` | A | Metadata lifecycle independent of data | — |
| `FAIRR-I1` | I | Structured formal metadata format | ✓ |
| `FAIRR-I2` | I | Controlled vocabularies and standard identifiers | — |
| `FAIRR-I3` | I | Links between research system components | ✓ |
| `FAIRR-I3-G` | I | Provenance graph completeness | — |
| `FAIRR-R1` | R | Content and structure documented | — |
| `FAIRR-R1.1` | R | License specified | ✓ |
| `FAIRR-R1.2` | R | Provenance documented | ✓ |
| `FAIRR-R1.3` | R | Community standards followed | — |
| `FAIRR-R1.3-02` | R | Recommended file formats used | — |
| `FAIRR-R1-S` | R | Explicit input/output interface specification | — |
| `FAIRR-R1-C` | R | Reuse context and limitations documented | — |
| `FAIRR-R2.1` | R2 | Environment captured | ✓ |
| `FAIRR-R2.2` | R2 | Configuration snapshot preserved | — |
| `FAIRR-R2.3` | R2 | Execution traceability | — |
| `FAIRR-R2.4` | R2 | Integrity verification available | ✓ |
| `FAIRR-R2.5` | R2 | Regeneration instructions documented | ✓ |

Scoring scale: 0–2 per metric. Principle weights: F 15%, A 15%, I 20%, R 20%, R2 30%.

## Architecture

```
fairrcheck/
├── cli.py          — Typer CLI (scan / advise / fix / info)
├── registry.py     — YAML-driven metric registry loader
├── scanner.py      — Orchestrates deterministic + LLM evaluation
├── detectors.py    — Heuristic detectors for each implemented metric
├── scoring.py      — Per-principle and weighted overall FAIRR score
├── llm.py          — OpenAI-compatible LLM client (stdlib urllib only)
├── agent.py        — Aider-style patch generation and application
├── templates/      — Jinja2 HTML template
└── reporters/      — JSON / HTML / PDF report writers
```

## How `advise` and `fix` Work

`advise` runs a deterministic scan, then sends the LLM:
- Low-scoring metric IDs, names, scores, and `max_score`
- Each metric's YAML **description** (what the metric measures)
- The detector's **evidence** and **rationale** (what was or wasn't found)
- Truncated excerpts of up to 5 project files (≤3 KB each)

The LLM returns up to 8 prioritised suggestions. `fix` takes those suggestions and generates unified diff patches, targeting `README.md`, `CITATION.cff`, or `metadata.json`.

## HPC Compatibility

- Pure Python stdlib for LLM HTTP calls (no `httpx`, `requests` dependency)
- No vLLM required — any OpenAI-compatible endpoint works
- Files >100 KB skipped automatically when building LLM context
- Sensitive files (`.env`, keys, credentials) never sent to LLM
- Fully offline deterministic scan (no network required without `--llm`)

## License

MIT
