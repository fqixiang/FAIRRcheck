"""
llm.py — OpenAI-compatible LLM client for SURF AI-Hub integration.

Environment variables
---------------------
FAIRRCHECK_LLM_BASE_URL          Optional.  Full base URL including any version
                                  segment.
                                  Default: https://willma.surf.nl/api/v0
FAIRRCHECK_LLM_API_KEY           Optional.  API key value.
FAIRRCHECK_LLM_AUTH_HEADER       Optional.  Header name for the API key.
                                  Default: X-API-KEY  (SURF Willma style).
                                  Use "Authorization" for OpenAI-style Bearer.
FAIRRCHECK_LLM_MODEL             Optional.  Model / sequence id.
                                  Default: openai/gpt-oss-120b
FAIRRCHECK_LLM_COMPLETIONS_PATH  Optional.  Path appended to base URL.
                                  Default: /chat/completions  (Willma style).

HPC constraints enforced here
------------------------------
- No requests outside the configured base_url.
- Files >100 KB are skipped automatically (caller's responsibility to filter).
- API key is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import Registry, load_registry

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 10_000   # max chars sent to LLM per file excerpt
_MAX_TOTAL_CHARS   = 100_000  # max total chars in a single prompt


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class LLMConfig:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        completions_path: Optional[str] = None,
        auth_header: Optional[str] = None,
    ) -> None:
        # Use base_url exactly as provided — no path manipulation.
        # For SURF Willma: https://willma.surf.nl/api/v0
        # For standard OpenAI: https://api.openai.com
        self.base_url = (
            base_url
            or os.environ.get("FAIRRCHECK_LLM_BASE_URL", "https://willma.surf.nl/api/v0")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("FAIRRCHECK_LLM_API_KEY", "")
        self.model = model or os.environ.get("FAIRRCHECK_LLM_MODEL", "openai/gpt-oss-120b")
        # SURF Willma appends /chat/completions directly to the base URL.
        # Standard OpenAI uses /v1/chat/completions; set the env var to override.
        self.completions_path = (
            completions_path
            or os.environ.get("FAIRRCHECK_LLM_COMPLETIONS_PATH", "/chat/completions")
        )
        # SURF Willma uses X-API-KEY; standard OpenAI uses Authorization Bearer.
        self.auth_header = (
            auth_header
            or os.environ.get("FAIRRCHECK_LLM_AUTH_HEADER", "X-API-KEY")
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def require(self) -> None:
        if not self.is_configured:
            raise RuntimeError(
                "LLM not configured. Set FAIRRCHECK_LLM_API_KEY to your API key."
            )


# ---------------------------------------------------------------------------
# Low-level HTTP call (no SDK dependency — uses only stdlib urllib)
# ---------------------------------------------------------------------------


def _chat_completion(
    config: LLMConfig,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> str:
    """
    Call the OpenAI-compatible /chat/completions endpoint.
    Returns the assistant message content string.
    """
    import urllib.request  # stdlib only — no external dependencies

    payload = json.dumps(
        {
            "model": config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.api_key:
        # SURF Willma: X-API-KEY <key>   OpenAI: Authorization: Bearer <key>
        if config.auth_header.lower() == "authorization":
            headers["Authorization"] = f"Bearer {config.api_key}"
        else:
            headers[config.auth_header] = config.api_key

    path = config.completions_path.lstrip("/")
    url = f"{config.base_url}/{path}"
    logger.debug("LLM request → %s  (auth header: %s)  model=%s", url, config.auth_header, config.model)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("LLM request failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _rescue_suggestions(raw: str) -> List[Dict[str, Any]]:
    """
    Last-resort: pull out every complete suggestion object from a
    truncated LLM response by scanning for balanced braces.
    """
    suggestions = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(raw):
        if raw[i] == "{":
            try:
                obj, end = decoder.raw_decode(raw, i)
                if isinstance(obj, dict) and "metric_id" in obj:
                    suggestions.append(obj)
                i = end
            except json.JSONDecodeError:
                i += 1
        else:
            i += 1
    return suggestions


def _extract_json(raw: str) -> Any:
    """
    Robustly extract a JSON value from *raw* text.

    Handles all common model misbehaviours in one place:
      1. Markdown code fences  (```json … ``` or ``` … ```)
      2. Leading prose before the JSON object/array
      3. Trailing prose / newlines after the closing brace
      4. Mixed: fences *and* surrounding prose
    """
    text = raw.strip()

    # --- strip opening fence (``` or ```json / ```JSON / …) ---
    if text.startswith("```"):
        newline = text.find("\n")
        text = (text[newline + 1:] if newline != -1 else text[3:]).strip()

    # --- strip closing fence ---
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()

    # --- fast path: the whole text is valid JSON ---
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # --- slow path: find the first { or [ and parse forward ---
    # json.JSONDecoder.raw_decode() parses the first complete JSON value at a
    # given offset and returns (value, end_index), ignoring trailing content.
    decoder = json.JSONDecoder()
    for start, ch in enumerate(text):
        if ch in ("{", "["):
            try:
                value, _ = decoder.raw_decode(text, start)
                return value
            except json.JSONDecodeError:
                continue  # try the next { / [

    # Nothing worked — re-raise a clean error
    raise json.JSONDecodeError("No JSON object found in LLM response", text, 0)


def llm_evaluate_metric(
    config: LLMConfig,
    metric_id: str,
    metric_name: str,
    metric_description: str,
    project_path: Path,
    file_excerpts: Dict[str, str],
    deterministic_score: int,
    max_score: int,
) -> Dict[str, Any]:
    """
    Ask the LLM to evaluate a single FAIRR metric.

    The LLM may only *raise* a deterministic score of 0; it cannot reduce
    existing non-zero scores.

    Returns a dict with: score_suggestion, evidence_excerpt, confidence, reasoning.
    """
    config.require()

    # Build excerpt block
    excerpt_parts: List[str] = []
    total = 0
    for fname, content in file_excerpts.items():
        snippet = _truncate(content, _MAX_CONTENT_CHARS)
        excerpt_parts.append(f"### {fname}\n{snippet}")
        total += len(snippet)
        if total >= _MAX_TOTAL_CHARS:
            break

    excerpt_block = "\n\n".join(excerpt_parts) or "(no file excerpts available)"

    system_prompt = textwrap.dedent(
        """
        You are a FAIRR compliance evaluator for HPC/computational research projects.
        You MUST respond with STRICT valid JSON only — no markdown, no prose.
        Use the exact schema provided. Do not add extra fields.
        """
    ).strip()

    user_prompt = textwrap.dedent(
        f"""
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
        {{
          "metric_id": "{metric_id}",
          "score_suggestion": <integer 0-{max_score}>,
          "evidence_excerpt": "<brief quote or filename from excerpts>",
          "confidence": <float 0.0-1.0>,
          "reasoning": "<one to three sentences>"
        }}
        """
    ).strip()

    raw = _chat_completion(
        config,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0.0,
        max_tokens=4096,
    )

    try:
        result = _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM returned non-JSON for %s: %s", metric_id, raw[:200])
        return {
            "metric_id": metric_id,
            "score_suggestion": deterministic_score,
            "evidence_excerpt": "",
            "confidence": 0.0,
            "reasoning": "LLM response was not valid JSON; keeping deterministic score.",
        }

    # Safety: never reduce a non-zero deterministic score
    if deterministic_score > 0:
        result["score_suggestion"] = max(result.get("score_suggestion", 0), deterministic_score)

    return result


def llm_advise(
    config: LLMConfig,
    scan_results: Dict[str, Any],
    project_path: Path,
    file_excerpts: Dict[str, str],
    registry: Optional[Registry] = None,
) -> Dict[str, Any]:
    """
    Ask the LLM for actionable improvement suggestions based on scan results.
    Returns: {"suggestions": [{metric_id, priority, message, example_snippet}]}
    """
    config.require()

    reg = registry or load_registry()
    desc_by_id = {m.id: m.description for m in reg.metrics}

    low_metrics = [
        r for r in scan_results.get("metrics", [])
        if r.get("score") is not None and r["score"] < scan_results.get("max_score", 2)
    ]
    low_summary = json.dumps(
        [
            {
                "metric_id": m["metric_id"],
                "name": m["name"],
                "description": desc_by_id.get(m["metric_id"], ""),
                "score": m["score"],
                "max_score": m.get("max_score", scan_results.get("max_score", 2)),
                "evidence": m.get("evidence", []),
                "rationale": m.get("rationale", ""),
            }
            for m in low_metrics[:15]
        ],
        indent=2,
    )

    excerpt_parts: List[str] = []
    total = 0
    for fname, content in list(file_excerpts.items())[:5]:
        snippet = _truncate(content, 3_000)
        excerpt_parts.append(f"### {fname}\n{snippet}")
        total += len(snippet)
        if total >= 12_000:
            break
    excerpt_block = "\n\n".join(excerpt_parts) or "(no excerpts)"

    system_prompt = (
        "You are a FAIRR compliance advisor. Respond with STRICT JSON only. "
        "No markdown, no prose outside the JSON."
    )

    user_prompt = textwrap.dedent(
        f"""
        Project path: {project_path}
        Overall FAIRR score: {scan_results.get('overall_fairr_score', 'N/A')}

        Low-scoring metrics:
        {low_summary}

        ==== File excerpts ====
        {excerpt_block}
        =======================

        Provide up to 8 actionable suggestions, prioritised by impact.
        Keep each message under 30 words. Keep example_snippet under 10 lines.

        Respond with ONLY this JSON:
        {{
          "suggestions": [
            {{
              "metric_id": "<FAIRR-XX>",
              "priority": <1-5, 1=highest>,
              "message": "<clear actionable advice, max 30 words>",
              "example_snippet": "<short example, max 10 lines, or empty string>"
            }}
          ]
        }}
        """
    ).strip()

    logger.debug(
        "llm_advise: sending %d low-scoring metrics to LLM. summary:\n%s",
        len(low_metrics),
        low_summary,
    )

    raw = _chat_completion(
        config,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0.1,
        max_tokens=4096,
    )

    logger.debug("llm_advise raw response:\n%s", raw)

    try:
        parsed = _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        parsed = {}

    # Happy path: got the expected wrapper
    if isinstance(parsed, dict) and parsed.get("suggestions"):
        return parsed

    # The JSON was truncated — raw_decode found the first suggestion dict
    # instead of the outer {"suggestions": [...]} wrapper. Rescue all
    # complete suggestion objects from the raw text.
    rescued = _rescue_suggestions(raw)
    if rescued:
        logger.debug("llm_advise: rescued %d suggestion(s) from truncated JSON", len(rescued))
        return {"suggestions": rescued}

    # Truly empty or unparseable
    logger.warning("LLM advise returned no usable suggestions:\n%s", raw[:1000])
    if not parsed:
        return {"suggestions": [], "error": f"LLM response was not valid JSON. Raw (first 500 chars): {raw[:500]}", "_raw": raw}
    logger.debug("llm_advise: LLM returned empty suggestions. Full parsed: %s", parsed)
    return {"suggestions": [], "_raw": raw}


def llm_generate_patch(
    config: LLMConfig,
    metric_id: str,
    message: str,
    file_content: str,
    filename: str,
) -> str:
    """
    Ask the LLM to generate a unified diff patch for *filename*.
    Returns the raw unified diff string (or empty string on failure).
    """
    config.require()

    system_prompt = (
        "You are a code patching assistant. "
        "Output ONLY a valid unified diff (--- / +++ / @@ format). "
        "No explanation, no markdown fences."
    )

    user_prompt = textwrap.dedent(
        f"""
        File: {filename}
        Improvement needed (metric {metric_id}): {message}

        Current file content:
        {_truncate(file_content, 10_000)}

        Produce a minimal unified diff that addresses the improvement.
        The diff must be applicable with `patch -p0`.
        """
    ).strip()

    try:
        raw = _chat_completion(
            config,
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=4096,
        )
        return raw.strip()
    except Exception as exc:
        logger.error("LLM patch generation failed: %s", exc)
        return ""
