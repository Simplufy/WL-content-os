"""Claude via the local Claude Code CLI (runs on the Claude subscription).

Auth: either an interactive `claude` login on this machine, or a long-lived token
from `claude setup-token` saved as CLAUDE_CODE_OAUTH_TOKEN in ROOT/.env.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import config, db


class LLMUnavailable(RuntimeError):
    """Auth/quota problem: retry later, don't fail the video."""


class LLMError(RuntimeError):
    pass


_AUTH_MARKERS = ("authenticate", "oauth", "login", "credit", "usage limit", "rate limit", "quota", "overloaded", "401", "403", "429", "529")


def _record_status(ok: bool, detail: str = "") -> None:
    try:
        db.set_setting("llm_status", {"ok": ok, "detail": detail[:300], "at": db.now()})
    except Exception:
        pass


def status() -> dict[str, Any]:
    return db.get_setting("llm_status", {"ok": None, "detail": "Not checked yet", "at": None})


def ask_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    system: str | None = None,
    read_dirs: list[Path] | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run one headless Claude call and return schema-validated JSON."""
    cmd = [
        config.CLAUDE, "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", model or config.LLM_MODEL,
        "--no-session-persistence",
        "--max-turns", "12",
    ]
    if system:
        cmd += ["--system-prompt", system]
    if read_dirs:
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        for d in read_dirs:
            cmd += ["--add-dir", str(d)]
    else:
        cmd += ["--tools", ""]

    workdir = config.MEDIA_DIR / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, env=env,
                             timeout=timeout or config.LLM_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise LLMError("Claude call timed out") from e

    out = (res.stdout or "").strip()
    try:
        payload = json.loads(out.splitlines()[-1]) if out else {}
    except json.JSONDecodeError:
        payload = {}
    text = str(payload.get("result") or res.stderr or out)[-600:]

    if payload.get("terminal_reason") == "aborted_streaming" or res.returncode < 0:
        raise LLMUnavailable("Claude call was interrupted (restart?) — will retry")
    if payload.get("is_error") or res.returncode != 0:
        if any(m in text.lower() for m in _AUTH_MARKERS):
            _record_status(False, text)
            raise LLMUnavailable(text)
        raise LLMError(text)

    data = payload.get("structured_output")
    if data is None:
        try:
            data = json.loads(payload.get("result") or "")
        except (TypeError, json.JSONDecodeError):
            raise LLMError("Claude returned no structured output: " + text[:300])
    _record_status(True)
    return data


def ping() -> dict[str, Any]:
    try:
        ask_json("Reply with ok=true.", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                 model="haiku", timeout=90)
    except (LLMUnavailable, LLMError) as e:
        _record_status(False, str(e))
    return status()
