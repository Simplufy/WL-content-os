"""Edit pipeline: prepare (once) → decide (Claude) → render (repeatable, fast)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import hashlib

from . import captions, decide, motion, plan, qa, render, sfx

Progress = Callable[[str, float], None]


def _noop(stage: str, frac: float) -> None:
    pass


# Priming Whisper with disfluent text makes it transcribe verbatim (keeps repeats and false
# starts) instead of silently tidying them, which would hide bad takes from the editor.
VERBATIM_PROMPT = "Umm, so, uh, I- I was, I was gonna say, the the the thing is, like, you know, it's- it's, hmm, okay so."
_model_cache: dict[str, Any] = {}


def _whisper(model_name: str):
    from faster_whisper import WhisperModel

    if model_name not in _model_cache:
        _model_cache[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=16)
    return _model_cache[model_name]


def transcribe_pcm(pcm, model_name: str = "medium") -> list[dict[str, Any]]:
    segs, _ = _whisper(model_name).transcribe(pcm, language="en", word_timestamps=True, vad_filter=False,
                                              initial_prompt=VERBATIM_PROMPT, condition_on_previous_text=False,
                                              beam_size=5)
    words = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": t, "p": round(w.probability, 3)})
    return words


def transcribe_windowed(pcm, sr: int = 16000, win: float = 10.0, overlap: float = 2.0,
                        model_name: str = "medium", phase: float = 0.0) -> list[dict[str, Any]]:
    """Verbatim transcript in short overlapping windows.

    With long context Whisper 'tidies' disfluencies away; on ~10s windows it reports stutters and
    half-words ("head— headlights"), which is exactly what the listen-back QA needs to hear.
    Each word belongs to the window whose core (window minus half the overlap) contains its midpoint.
    """
    total = len(pcm) / sr
    if total <= win + overlap:
        return transcribe_pcm(pcm, model_name)
    out: list[dict[str, Any]] = []
    step = win - overlap
    t = 0.0
    if phase:  # first window shorter so every boundary moves (a second pass hears what the first missed)
        first = transcribe_pcm(pcm[: int(phase * sr)], model_name) if phase > 1 else []
        out += [w for w in first if (w["start"] + w["end"]) / 2 < phase - overlap / 2]
        t = phase - overlap
    while t < total:
        a, b = t, min(total, t + win)
        core_a = a + (overlap / 2 if a > 0 else 0)
        core_b = b - (overlap / 2 if b < total else 0)
        for w in transcribe_pcm(pcm[int(a * sr): int(b * sr)], model_name):
            mid = a + (w["start"] + w["end"]) / 2
            if core_a <= mid < core_b or (b >= total and mid >= core_a):
                out.append({**w, "start": round(a + w["start"], 3), "end": round(a + w["end"], 3)})
        if b >= total:
            break
        t += step
    return out


def transcribe_words(audio16: Path, model_name: str = "medium") -> list[dict[str, Any]]:
    pcm, _sr = render.read_wav(audio16)  # pass samples: faster-whisper's own decoder breaks on newer PyAV
    return transcribe_pcm(pcm, model_name)


def prepare(src: Path, work: Path, progress: Progress = _noop) -> dict[str, Any]:
    """Probe, SDR master, audio, transcript, face track. Results cached in `work`."""
    work.mkdir(parents=True, exist_ok=True)
    p = render.probe(src)
    progress("Preparing master", 0.05)
    master = work / "master.mp4"
    if not master.exists():
        tmp = work / "master.partial.mp4"
        render.make_master(src, tmp, p)
        tmp.rename(master)  # only a finished master counts as cached
    progress("Extracting audio", 0.45)
    a48, a16 = work / "audio48.wav", work / "audio16.wav"
    for path, rate in ((a48, render.SR), (a16, 16000)):
        if not path.exists():
            tmp = path.with_suffix(".partial.wav")
            render.extract_audio(src, tmp, sr=rate)
            tmp.rename(path)
    progress("Transcribing", 0.55)
    wf = work / "words.json"
    if not wf.exists():
        wf.write_text(json.dumps(transcribe_words(a16)))
    progress("Tracking face", 0.85)
    ff = work / "faces.json"
    if not ff.exists():
        ff.write_text(json.dumps(render.face_track(master, render.master_size(p))))
    progress("Ready", 1.0)
    return {"probe": p, "words": json.loads(wf.read_text()), "faces": json.loads(ff.read_text())}


DEFAULT_OPTIONS = {
    "captions": True,
    "caption_style": "brand",
    "caption_position": "lower",
    "caption_words": 3,
    "caption_size": "M",
    "caption_case": None,
    "graphics": True,
    "graphics_every": 6,
    "sfx": True,
    "sfx_db": -22.0,
    "callouts": False,
    "hook_title": True,
    "zooms": True,
    "zoom_mode": "both",
    "zoom_alt": 1.07,
    "zoom_punch": 1.18,
    "max_pause": 0.26,
    "color_grade": "natural",
    "style_id": None,
}


def _grade(name: str) -> str:
    from ..styles import GRADES
    return GRADES.get(name or "natural", "")


def keep_ranges(decision: dict[str, Any], overrides: dict[str, Any] | None, n_words: int) -> list[tuple[int, int]]:
    """Claude's keep ranges, adjusted by the owner's manual restores/removals (word-level)."""
    ranges = [(r["from"], r["to"]) for r in decision.get("keep") or []]
    if not overrides:
        return ranges
    removed = set(overrides.get("remove") or [])
    restored = set(overrides.get("restore") or [])
    kept_order: list[int] = []
    seen = set()
    for a, b in ranges:
        for i in range(a, b + 1):
            if i not in seen:
                kept_order.append(i)
                seen.add(i)
    # restored words slot in chronologically next to their neighbours
    for i in sorted(restored - seen):
        pos = next((k for k, j in enumerate(kept_order) if j > i), len(kept_order))
        kept_order.insert(pos, i)
    kept_order = [i for i in kept_order if i not in removed and 0 <= i < n_words]
    out: list[tuple[int, int]] = []
    for i in kept_order:
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def render_edit(work: Path, words: list[dict[str, Any]], faces: list[dict[str, Any]], probe: dict[str, Any],
                decision: dict[str, Any], options: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None,
                progress: Progress = _noop, out_name: str = "final.mp4") -> dict[str, Any]:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    t0 = time.time()
    audio, sr = render.read_wav(work / "audio48.wav")
    env = plan.energy_envelope(audio, sr)
    ranges = keep_ranges(decision, overrides, len(words))
    segs = plan.build_timeline(words, ranges, env, max_pause=opts["max_pause"], duration=probe["duration"])
    segs = plan.remove_overlaps(plan.compress_silences(segs, env, words, min_gap=opts["max_pause"] * 0.8))
    if not segs:
        raise render.RenderError("Nothing left to render — every word was cut")
    emph = set(decision.get("emphasis_words") or []) if opts["zooms"] else set()
    plan.assign_zoom(segs, emph, alt=opts["zoom_alt"], punch=opts["zoom_punch"],
                     mode=opts["zoom_mode"] if opts["zooms"] else "none")

    # Listen-back QA: up to 2 passes removing speech that shouldn't be in the cut.
    qa_log = []
    intended = [words[i]["word"] for s in segs for i in s.words]
    for rnd in range(4 if opts.get("qa", True) else 0):
        progress("Checking the cut", 0.02 + 0.01 * rnd)
        cut16 = qa.to_16k(render.assemble_audio(audio, sr, segs))
        got = transcribe_windowed(cut16)
        got_b = transcribe_windowed(cut16, phase=5.0)
        cut_env = plan.energy_envelope(cut16, 16000)
        left = qa.merge_ranges(
            qa.combine_passes(qa.find_leftovers_detailed(intended, got), qa.find_leftovers_detailed(intended, got_b))
            + qa.unheard_islands(got, cut_env, plan.ENV_HOP, also_heard=got_b))
        if not left:
            break
        src = qa.out_to_src(left, segs)
        removed_text = [" ".join(w["word"] for w in got if a - 0.01 <= w["start"] and w["end"] <= b + 0.01) for a, b in left]
        qa_log.append({"round": rnd + 1, "removed": removed_text, "source_ranges": src})
        segs = plan.remove_overlaps(qa.apply_exclusions(segs, words, src))
    size = render.master_size(probe)
    base_zoom = render.base_zoom_for(faces, probe["portrait"])

    progress("Cutting video", 0.05)
    video = render.render_segments(work / "master.mp4", segs, faces, size, work / "segs", base_zoom,
                                   progress=lambda f: progress("Cutting video", 0.05 + 0.6 * f),
                                   grade=_grade(opts["color_grade"]))
    progress("Mixing audio", 0.7)
    cut_audio = render.assemble_audio(audio, sr, segs)
    raw_wav, final_wav = work / "cut_raw.wav", work / "cut_final.wav"
    render.write_wav(raw_wav, cut_audio, sr)
    loud = render.master_audio(raw_wav, final_wav)

    progress("Captions & graphics", 0.8)
    out_words = plan.output_word_times(words, segs)
    dur = plan.total_duration(segs)
    placed = captions.place_callouts(decision.get("callout_words") or [], out_words) if opts["callouts"] else []
    ass_hook = decision.get("hook_text") if opts["hook_title"] and not opts["graphics"] else None
    chin = render.typical_chin(segs, faces, size, base_zoom)
    ass_text = captions.build_ass(out_words, callouts=placed, hook_text=ass_hook,
                                  style=opts["caption_style"], captions=opts["captions"], duration=dur,
                                  position=opts["caption_position"], max_words=opts["caption_words"],
                                  size=opts["caption_size"], case=opts["caption_case"], chin_y=chin)
    cap_top = captions.caption_top(opts["caption_position"], opts["caption_size"], opts["caption_style"], chin) \
        if opts["captions"] else 1800
    ass = work / "overlay.ass"
    ass.write_text(ass_text)
    overlays: list[dict[str, Any]] = []
    review_log: list[dict[str, Any]] = []
    if opts["graphics"]:
        progress("Animating graphics", 0.82)
        hook_on = bool(opts["hook_title"])
        overlays = build_graphics(work, words, segs, out_words, faces, size, base_zoom, decision, hook=hook_on,
                                  every=opts["graphics_every"], caption_top=cap_top)
        plan_cache = json.loads((work / "graphics_plan.json").read_text())
        if opts.get("critique", True) and not plan_cache.get("approved"):
            overlays, review_log = critique_graphics(work, video, overlays, ass, out_words, progress=progress)
            plan_cache = json.loads((work / "graphics_plan.json").read_text())
            plan_cache["approved"] = True
            plan_cache["review"] = review_log
            plan_cache["final"] = [{k: v for k, v in o.items() if k != "file"} for o in overlays]
            (work / "graphics_plan.json").write_text(json.dumps(plan_cache, indent=1))
    mix_wav = final_wav
    if overlays and opts.get("sfx", True):
        voice, vsr = render.read_wav(final_wav)
        fx = sfx.render_track(sfx.cues_for(overlays), len(voice) / vsr, level_db=opts["sfx_db"])
        mix_wav = work / "cut_mix.wav"
        render.write_wav(mix_wav, sfx.mix_into(voice, fx), vsr)
    progress("Final encode", 0.9)
    out = work / out_name
    render.final_mux(video, mix_wav, ass, out, overlays)
    render.thumbnail(out, work / "thumb.jpg")
    progress("Done", 1.0)
    return {
        "output": str(out),
        "duration": dur,
        "segments": [s.to_dict() for s in segs],
        "kept_words": [w["i"] for w in out_words],
        "callouts": placed,
        "graphics": [{k: g[k] for k in ("type", "label", "text", "start", "end") if k in g} for g in overlays],
        "graphics_review": review_log,
        "loudness_in": loud.get("input_i"),
        "render_s": round(time.time() - t0, 1),
        "qa": qa_log,
    }


def _timed_transcript(out_words: list[dict[str, Any]]) -> str:
    lines, cur = [], []
    for w in out_words:
        if not cur:
            cur.append(f"[{w['start']:.1f}s]")
        cur.append(w["word"])
        if w["word"].endswith((".", "?", "!")) or len(cur) > 16:
            lines.append(" ".join(cur))
            cur = []
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines)


TALL = {"traffic_light": 520, "bulb_check": 500, "steps": 520, "checklist": 500, "before_after": 460, "word_slam": 420}


def _band_for(g: dict[str, Any], segs: list[plan.Segment], faces: list[dict[str, Any]], size: tuple[int, int],
              base_zoom: float, caption_top: int = render.CAPTION_TOP) -> dict[str, int]:
    """Where this graphic goes: whichever free area (above head / below chin) suits it at that moment."""
    if g.get("type") == "hook_title":
        return render.best_band(segs[0], faces, size, base_zoom, want=380, caption_top=caption_top)
    t, mid = 0.0, g["start"] + 0.5
    seg = segs[0]
    for s in segs:
        if t <= mid < t + s.dur:
            seg = s
            break
        t += s.dur
    return render.best_band(seg, faces, size, base_zoom, want=TALL.get(g.get("type", ""), 380), caption_top=caption_top)


def _reanchor(final: list[dict[str, Any]], words: list[dict[str, Any]], kept_order: list[int],
              out_words: list[dict[str, Any]], total: float) -> list[dict[str, Any]] | None:
    """Move approved graphics onto a changed cut by their anchor phrases. None if too many no longer fit."""
    by_i = {w["i"]: w for w in out_words}
    out, lost = [], 0
    for g in final:
        if g.get("type") == "hook_title":
            out.append({**g, "start": 0.0, "end": min(3.2, total)})
            continue
        hit = decide.find_anchor(words, kept_order, g.get("anchor", "")) if g.get("anchor") else None
        if not hit or hit[0] not in by_i:
            lost += 1
            continue
        start = max(0.0, by_i[hit[0]]["start"] - 0.12)
        out.append({**g, "start": round(start, 3), "end": round(start + (g["end"] - g["start"]), 3)})
    if lost > max(1, len(final) // 4):
        return None
    out.sort(key=lambda g: g["start"])
    fixed = []
    for k, g in enumerate(out):
        nxt = out[k + 1]["start"] if k + 1 < len(out) else total
        g["end"] = min(g["end"], nxt - 0.15, total)
        if g["end"] - g["start"] >= 1.2:
            fixed.append(g)
    return fixed


def build_graphics(work: Path, words: list[dict[str, Any]], segs: list[plan.Segment], out_words: list[dict[str, Any]],
                   faces: list[dict[str, Any]], size: tuple[int, int], base_zoom: float, decision: dict[str, Any],
                   *, hook: bool, force_replan: bool = False, feedback: str | None = None, every: float = 6,
                   caption_top: int = render.CAPTION_TOP) -> list[dict[str, Any]]:
    """Plan (Claude, cached per cut) → place on the timeline → render alpha clips."""
    kept_order = [w["i"] for w in out_words]
    key = hashlib.sha1(json.dumps([kept_order, every]).encode()).hexdigest()[:12]
    cache = work / "graphics_plan.json"
    plan_data = json.loads(cache.read_text()) if cache.exists() else {}
    if not feedback and not force_replan and plan_data.get("approved") and plan_data.get("final") is not None:
        reused = _reanchor(plan_data["final"], words, kept_order, out_words, plan.total_duration(segs)) \
            if plan_data.get("every", 6) == every else None
        if reused is not None:
            # reviewed set still fits this cut: re-time to the new edit, recompute placement, keep approval
            for g in reused:
                g["band"] = _band_for(g, segs, faces, size, base_zoom, caption_top)
            plan_data.update(key=key, final=[{k: v for k, v in g.items() if k != "file"} for g in reused])
            cache.write_text(json.dumps(plan_data, indent=1))
            return motion.render_graphics(reused, work / "gfx")
    if feedback or force_replan or plan_data.get("key") != key or plan_data.get("v") != 2:
        ctx = f"Video: {decision.get('title', '')}"
        if feedback and plan_data.get("graphics"):
            ctx += "\nPrevious plan:\n" + json.dumps({"hook": plan_data.get("hook"), "graphics": plan_data["graphics"]}, indent=1)
        planned = decide.plan_graphics(_timed_transcript(out_words), icon_names=motion.curated_icons(),
                                       dash_icons=motion.DASH_ICONS, context=ctx, feedback=feedback, every_s=every)
        plan_data = {"key": key, "v": 2, "every": every, **planned, "reviewed": bool(feedback)}
        cache.write_text(json.dumps(plan_data, indent=1))

    by_i = {w["i"]: w for w in out_words}
    placed: list[dict[str, Any]] = []
    for g in plan_data.get("graphics") or []:
        hit = decide.find_anchor(words, kept_order, g.get("anchor", ""))
        if not hit or hit[0] not in by_i:
            continue
        start = max(0.0, by_i[hit[0]]["start"] - 0.12)
        if hook and start < 3.35:
            continue
        placed.append({**g, "start": round(start, 3), "end": round(start + float(g.get("duration_s") or 2.5), 3)})
    placed.sort(key=lambda g: g["start"])
    total = plan.total_duration(segs)
    final: list[dict[str, Any]] = []
    for k, g in enumerate(placed):
        nxt = placed[k + 1]["start"] if k + 1 < len(placed) else total
        g["end"] = min(g["end"], nxt - 0.15, total)
        if g["end"] - g["start"] < 1.2 or (final and g["start"] < final[-1]["end"]):
            continue
        g["band"] = _band_for(g, segs, faces, size, base_zoom, caption_top)
        final.append(g)
    h = plan_data.get("hook") or {}
    if hook and h.get("text"):
        hk = {"type": "hook_title", "eyebrow": h.get("eyebrow", ""), "text": h["text"], "start": 0.0, "end": min(3.2, total)}
        hk["band"] = _band_for(hk, segs, faces, size, base_zoom, caption_top)
        final.insert(0, hk)
    rendered = motion.render_graphics(final, work / "gfx")
    return rendered


def critique_graphics(work: Path, video: Path, overlays: list[dict[str, Any]], ass: Path, out_words: list[dict[str, Any]],
                      rounds: int = 2, progress: Progress = _noop) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Contact sheet → Claude scores every graphic → revise only the failing ones → re-review.

    Graphics that pass are frozen. Anything still failing after the last round is dropped,
    so nothing unreviewed ships.
    """
    from . import critique

    log: list[dict[str, Any]] = []
    transcript = _timed_transcript(out_words)
    for rnd in range(rounds):
        if not overlays:
            break
        progress("Reviewing graphics", 0.86 + rnd * 0.01)
        sheet = critique.contact_sheet(video, overlays, work / "review" / f"sheet_{rnd + 1}.png", ass)
        rev = critique.review(sheet, overlays, transcript)
        bad = [r for r in critique.failing(rev) if 0 < r["n"] <= len(overlays)]
        log.append({"round": rnd + 1, "scores": rev.get("graphics"), "overall": rev.get("overall"), "failing": len(bad)})
        if not bad:
            break
        bad_idx = {r["n"] - 1 for r in bad}
        if rnd == rounds - 1:
            overlays = [o for k, o in enumerate(overlays) if k not in bad_idx]
            log[-1]["dropped"] = sorted(n + 1 for n in bad_idx)
            break
        fixes = decide.fix_graphics([{"n": r["n"], "spec": overlays[r["n"] - 1], "problem": r["problem"], "fix": r["fix"]} for r in bad],
                                    transcript, icon_names=motion.curated_icons(), dash_icons=motion.DASH_ICONS)
        by_n = {f["n"]: f for f in fixes}
        keep, redo = [], []
        for k, o in enumerate(overlays):
            if k not in bad_idx:
                keep.append(o)
                continue
            f = by_n.get(k + 1)
            if not f or f.get("drop"):
                continue
            g = {**f["graphic"], "start": o["start"], "end": o["end"], "band": o["band"]}
            if o["type"] == "hook_title":
                g = {**o, **{k2: v for k2, v in f["graphic"].items() if k2 in ("eyebrow", "text", "label") and v}}
                g["type"] = "hook_title"
            redo.append(g)
        redone = motion.render_graphics(redo, work / "gfx", prefix=f"fix{rnd + 1}") if redo else []
        overlays = sorted(keep + redone, key=lambda o: o["start"])
    return overlays, log


SAMPLE_GRAPHICS = {
    "high": {"type": "checklist", "eyebrow": "The framework", "items": ["Know your *numbers*", "Build the *system*", "Hire the *role*"]},
    "medium": {"type": "stat", "value": "3x", "eyebrow": "Results", "sub": "in the first 90 days"},
    "low": {"type": "icon", "icon": "lightbulb", "eyebrow": "Quick tip", "label": "*Start* here"},
}
DENSITY_BY_EVERY = {4: "high", 6: "medium", 10: "low"}


def render_preview(work: Path, probe: dict[str, Any], decision: dict[str, Any], options: dict[str, Any], out_gif: Path,
                   seconds: float = 7.0) -> Path:
    """A short GIF of what a style does to real footage: first ~7s of the cut, with the style's captions,
    pacing, zooms, grade, hook card and one sample graphic. No LLM calls."""
    import shutil
    import tempfile

    opts = {**DEFAULT_OPTIONS, **options}
    words = json.loads((work / "words.json").read_text())
    faces = json.loads((work / "faces.json").read_text())
    audio, sr = render.read_wav(work / "audio48.wav")
    env = plan.energy_envelope(audio, sr)
    segs = plan.build_timeline(words, keep_ranges(decision, None, len(words)), env, max_pause=opts["max_pause"],
                               duration=probe["duration"])
    segs = plan.remove_overlaps(plan.compress_silences(segs, env, words, min_gap=opts["max_pause"] * 0.8))
    plan.assign_zoom(segs, set(decision.get("emphasis_words") or []), alt=opts["zoom_alt"], punch=opts["zoom_punch"],
                     mode=opts["zoom_mode"] if opts["zooms"] else "none")
    # the first `seconds` of the edit
    keep, t = [], 0.0
    for sgm in segs:
        if t >= seconds:
            break
        if t + sgm.dur > seconds:
            sgm = plan.Segment(sgm.src_in, plan.frame(sgm.src_in + seconds - t), sgm.zoom, sgm.words, sgm.range_id)
        keep.append(sgm)
        t += sgm.dur
    segs = [s for s in keep if s.dur > 0.05]
    size = render.master_size(probe)
    base_zoom = render.base_zoom_for(faces, probe["portrait"])
    tmp = Path(tempfile.mkdtemp(prefix="preview-", dir=work))
    try:
        video = render.render_segments(work / "master.mp4", segs, faces, size, tmp / "segs", base_zoom,
                                       grade=_grade(opts["color_grade"]))
        out_words = [w for w in plan.output_word_times(words, segs) if w["start"] < seconds]
        dur = plan.total_duration(segs)
        ass = tmp / "overlay.ass"
        chin = render.typical_chin(segs, faces, size, base_zoom)
        ass.write_text(captions.build_ass(out_words, style=opts["caption_style"], captions=opts["captions"], duration=dur,
                                          position=opts["caption_position"], max_words=opts["caption_words"],
                                          size=opts["caption_size"], case=opts["caption_case"], chin_y=chin))
        cap_top = captions.caption_top(opts["caption_position"], opts["caption_size"], opts["caption_style"], chin)
        gfx: list[dict[str, Any]] = []
        plan_file = work / "graphics_plan.json"
        hook = (json.loads(plan_file.read_text()).get("hook") if plan_file.exists() else None) or {}
        if opts["hook_title"] and (hook.get("text") or decision.get("hook_text")):
            g = {"type": "hook_title", "eyebrow": hook.get("eyebrow", ""), "text": hook.get("text") or decision["hook_text"],
                 "start": 0.0, "end": min(3.2, dur)}
            g["band"] = _band_for(g, segs, faces, size, base_zoom, cap_top)
            gfx.append(g)
        sample = SAMPLE_GRAPHICS.get(DENSITY_BY_EVERY.get(int(opts.get("graphics_every") or 6), "medium"))
        if opts["graphics"] and sample and dur > 4.5:
            g = {**sample, "start": 3.4, "end": min(dur - 0.15, 6.9)}
            g["band"] = _band_for(g, segs, faces, size, base_zoom, cap_top)
            gfx.append(g)
        overlays = motion.render_graphics(gfx, tmp / "gfx") if gfx else []
        mp4 = tmp / "preview.mp4"
        render.final_mux(video, None, ass if opts["captions"] else None, mp4, overlays)
        out_gif.parent.mkdir(parents=True, exist_ok=True)
        render._run([render.config.FFMPEG, "-y", "-v", "error", "-i", str(mp4), "-vf",
                     "fps=10,scale=270:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
                     "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", str(out_gif)])
        render._run([render.config.FFMPEG, "-y", "-v", "error", "-i", str(mp4), "-c:v", "libx264", "-crf", "26",
                     "-vf", "scale=540:-2", "-an", "-movflags", "+faststart", str(out_gif.with_suffix(".mp4"))])
        return out_gif
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
