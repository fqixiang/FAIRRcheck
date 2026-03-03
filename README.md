# fairrcheck

**FAIRR compliance checker for HPC / computational research projects.**

Built as a prototype for the [SURF](https://www.surf.nl/) HPC research-software interview.

---

## FAIRR — What it is

FAIRR extends the FAIRsFAIR principles (Findable, Accessible, Interoperable, Reusable)
with an explicit **Reproducible (R2)** dimension, tailored for computational research on HPC.

The full metric registry lives in [`config/metrics_fairr_v1.yml`](config/metrics_fairr_v1.yml)
and is the **single source of truth** — no metrics are hard-coded anywhere in the Python code.

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

# Scan the "fairrish" example project (deterministic)
fairrcheck scan examples/fairrish_project --mode development

# Scan the "unfair" example project
fairrcheck scan examples/unfair_project

# Augment scan with LLM (requires SURF AI-Hub configuration)
export FAIRRCHECK_LLM_BASE_URL=https://api.ai.surf.nl
export FAIRRCHECK_LLM_MODEL=meta-llama/Llama-3-70b-Instruct
fairrcheck scan examples/fairrish_project --llm

# LLM-powered improvement advice
fairrcheck advise examples/unfair_project

# Generate Aider-style fix patches (dry-run)
fairrcheck fix examples/unfair_project

# Generate and apply patches
fairrcheck fix examples/unfair_project --apply
```

## Output

All reports are written to `<project_path>/fairrcheck_out/`:

| File | Description |
|------|-------------|
| `report.json` | Full structured JSON result |
| `report.html` | Visual HTML report with per-principle breakdown |
| `report.pdf` | Concise PDF summary (ReportLab) |
| `advice.json` | LLM advisory output (`advise` command) |
| `patches.json` | Generated diff patches (`fix` command) |

## Environment Variables (LLM / SURF AI-Hub)

| Variable | Required | Description |
|----------|----------|-------------|
| `FAIRRCHECK_LLM_BASE_URL` | Yes (for LLM) | OpenAI-compatible endpoint base URL |
| `FAIRRCHECK_LLM_API_KEY` | No | Bearer token (if endpoint requires auth) |
| `FAIRRCHECK_LLM_MODEL` | Yes (for LLM) | Model name (e.g. `meta-llama/Llama-3-70b-Instruct`) |

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

## HPC Compatibility

- Pure Python stdlib for LLM HTTP calls (no `httpx`, `requests` dependency)
- No vLLM required — any OpenAI-compatible endpoint works
- Files >100 KB skipped automatically when building LLM context
- Sensitive files (`.env`, keys, credentials) never sent to LLM
- Fully offline deterministic scan (no network required without `--llm`)

## License

MIT
