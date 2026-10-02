"""Edit projects: job handlers around studio.editor.engine."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import config, db, jobs
from .editor import decide, engine

JOB_KINDS = ("edit_prepare", "edit_decide", "edit_render")
EDITS_DIR = config.MEDIA_DIR / "edits"


def _p(pid: int) -> dict[str, Any]:
    p = db.row("SELECT * FROM edit_projects WHERE id = ?", (pid,))
    if not p:
        raise LookupError(f"edit project {pid} not found")
    return p


def _set(pid: int, **fields: Any) -> None:
    fields["updated_at"] = db.now()
    db.update("edit_projects", pid, fields)


def progress_cb(pid: int):
    def cb(stage: str, frac: float) -> None:
        _set(pid, stage=stage, progress=round(frac, 3))
    return cb


def style_options(style_id: int | None) -> dict[str, Any]:
    from . import styles
    if not style_id:
        return {}
    st = db.row("SELECT params_json FROM edit_styles WHERE id = ?", (style_id,))
    return {**styles.to_options(json.loads(st["params_json"])), "style_id": style_id} if st else {}


def create(source: Path, *, title: str | None = None, idea_id: int | None = None, instruction: str | None = None,
           target_s: int | None = None, move: bool = False, style_id: int | None = None) -> int:
    pid = db.execute(
        "INSERT INTO edit_projects(title, idea_id, source_path, source_name, work_dir, status, instruction, target_s, "
        "options_json, created_at, updated_at) VALUES(?,?,?,?,?, 'queued', ?, ?, ?, ?, ?)",
        (title or source.stem, idea_id, "", source.name, "", instruction, target_s,
         json.dumps({**engine.DEFAULT_OPTIONS, **style_options(style_id)}),
         db.now(), db.now()),
    )
    work = EDITS_DIR / str(pid)
    work.mkdir(parents=True, exist_ok=True)
    dest = work / ("source" + source.suffix.lower())
    if move:
        shutil.move(str(source), dest)
    else:
        try:
            dest.hardlink_to(source)
        except OSError:
            shutil.copy2(source, dest)
    _set(pid, source_path=str(dest), work_dir=str(work))
    jobs.enqueue("edit_prepare", pid, priority=7, max_attempts=2)
    db.log_event(f"New edit: {title or source.name}")
    return pid


def prepare(pid: int, **_: Any) -> None:
    p = _p(pid)
    _set(pid, status="preparing", error=None)
    res = engine.prepare(Path(p["source_path"]), Path(p["work_dir"]), progress=progress_cb(pid))
    _set(pid, probe_json=json.dumps(res["probe"]), stage="Waiting for the edit decision", progress=None)
    jobs.enqueue("edit_decide", pid, priority=8, max_attempts=2)


def _words(p: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads((Path(p["work_dir"]) / "words.json").read_text())


def decide_edit(pid: int, **_: Any) -> None:
    p = _p(pid)
    _set(pid, status="deciding", stage="Claude is choosing the takes", progress=None, error=None)
    script = None
    if p["idea_id"]:
        idea = db.row("SELECT script_json FROM ideas WHERE id = ?", (p["idea_id"],))
        script = json.loads(idea["script_json"]) if idea and idea["script_json"] else None
    d = decide.decide(_words(p), script=script, instruction=p["instruction"], target_s=p["target_s"])
    _set(pid, decision_json=json.dumps(d), overrides_json=None, title=p["title"] if p["title"] != Path(p["source_name"] or "").stem else d.get("title") or p["title"])
    jobs.enqueue("edit_render", pid, priority=8, max_attempts=2)


def render_edit(pid: int, **_: Any) -> None:
    p = _p(pid)
    _set(pid, status="rendering", error=None)
    work = Path(p["work_dir"])
    res = engine.render_edit(
        work, _words(p), json.loads((work / "faces.json").read_text()), json.loads(p["probe_json"]),
        json.loads(p["decision_json"]), json.loads(p["options_json"] or "{}"), json.loads(p["overrides_json"] or "null"),
        progress=progress_cb(pid),
    )
    _set(pid, status="ready", stage=None, progress=None, result_json=json.dumps(res), render_count=(p["render_count"] or 0) + 1)
    if p["idea_id"]:
        db.execute("UPDATE ideas SET stage = 'edited', updated_at = ? WHERE id = ? AND stage IN ('idea','scripted','filmed')",
                   (db.now(), p["idea_id"]))
    db.log_event(f"Edit ready: {p['title']} ({res['duration']:.0f}s)")


def mark(kind: str, pid: int | None, status: str, error: str) -> None:
    if pid is not None:
        _set(pid, status=status, error=error[:1500], stage=None, progress=None)


HANDLERS = {"edit_prepare": prepare, "edit_decide": decide_edit, "edit_render": render_edit}
