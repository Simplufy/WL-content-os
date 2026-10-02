"""Media work for the editor: probe, SDR master, face track, segment render, audio, final mux."""
from __future__ import annotations

import json
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import config
from .plan import FPS, Segment

OUT_W, OUT_H = 1080, 1920
SR = 48000
FACE_MODEL = config.ROOT / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
FONTS_DIR = config.ROOT / "assets" / "fonts"


class RenderError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RenderError(f"{Path(cmd[0]).name} failed: {(res.stderr or res.stdout)[-800:]}")
    return res


# --------------------------------------------------------------------------- probe

def probe(path: Path) -> dict[str, Any]:
    res = _run([config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120)
    d = json.loads(res.stdout)
    v = next((s for s in d["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in d["streams"] if s.get("codec_type") == "audio"), None)
    if not v:
        raise RenderError("No video stream found")
    if not a:
        raise RenderError("No audio track found — the editor cuts on speech")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {
        "duration": float(d["format"]["duration"]),
        "width": w,
        "height": h,
        "portrait": h >= w,
        "hdr": v.get("color_transfer") in ("arib-std-b67", "smpte2084"),
        "codec": v.get("codec_name"),
        "fps": v.get("r_frame_rate"),
        "size": int(d["format"].get("size") or 0),
    }


def master_size(p: dict[str, Any]) -> tuple[int, int]:
    """Working resolution: big enough for ~1.5x punch-ins without upscaling."""
    if p["portrait"]:
        w = min(1620, p["width"] - p["width"] % 2)
        h = int(round(w * p["height"] / p["width"] / 2) * 2)
    else:
        h = min(2880, p["height"] - p["height"] % 2)
        w = int(round(h * p["width"] / p["height"] / 2) * 2)
    return w, h


def make_master(src: Path, out: Path, p: dict[str, Any]) -> None:
    """Decode once: tone-map HDR to SDR Rec.709, scale, constant 30fps, short GOP for fast seeking."""
    w, h = master_size(p)
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-g", "15", "-bf", "0", "-pix_fmt", "yuv420p",
            "-an", str(out)]
    placebo = (f"libplacebo=w={w}:h={h}:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p"
               + (":tonemapping=bt.2390" if p["hdr"] else "") + f",fps={FPS}")
    try:
        _run([config.FFMPEG, "-y", "-v", "error", "-init_hw_device", "vulkan", "-i", str(src), "-map", "0:v:0",
              "-vf", placebo, *tail])
    except RenderError:
        if p["hdr"]:
            vf = (f"zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0:param=0.35,"
                  f"zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale={w}:{h}:flags=lanczos,fps={FPS}")
        else:
            vf = f"scale={w}:{h}:flags=lanczos,format=yuv420p,fps={FPS}"
        _run([config.FFMPEG, "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-vf", vf, *tail])


def extract_audio(src: Path, out: Path, sr: int = SR) -> None:
    _run([config.FFMPEG, "-y", "-v", "error", "-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr),
          "-c:a", "pcm_s16le", str(out)], 600)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------- face track

def face_track(master: Path, size: tuple[int, int], every_s: float = 0.5) -> list[dict[str, float]]:
    """Normalized face centre/height sampled through the master."""
    import cv2

    w, h = size
    sw = 360
    sh = int(round(sw * h / w / 2) * 2)
    proc = subprocess.Popen(
        [config.FFMPEG, "-v", "error", "-i", str(master), "-vf", f"fps={1 / every_s},scale={sw}:{sh}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE,
    )
    det = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (sw, sh), 0.6)
    out, k, fsz = [], 0, sw * sh * 3
    assert proc.stdout
    while True:
        buf = proc.stdout.read(fsz)
        if len(buf) < fsz:
            break
        img = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
        _, faces = det.detect(img)
        if faces is not None and len(faces):
            f = max(faces, key=lambda r: r[2] * r[3])
            out.append({"t": round(k * every_s, 2), "cx": float((f[0] + f[2] / 2) / sw),
                        "cy": float((f[1] + f[3] / 2) / sh), "fh": float(f[3] / sh)})
        k += 1
    proc.wait()
    return out


def base_zoom_for(faces: list[dict[str, float]], portrait: bool) -> float:
    """Frame so the face is ~28% of the height; never zoom out past the source."""
    if not faces:
        return 1.0
    fh = float(np.median([f["fh"] for f in faces]))
    target = 0.28 if portrait else 0.30
    return float(np.clip(target / max(fh, 1e-3), 1.0, 1.6))


def crop_for(seg: Segment, faces: list[dict[str, float]], size: tuple[int, int], base_zoom: float,
             max_face: float = 0.42) -> tuple[int, int, int, int]:
    """Static 9:16 crop for a segment, face held ~40% from the top.

    Zoom is reduced when it would make the face too big, when the face can't be kept
    away from the frame edge, or when the camera moves a lot during the segment.
    """
    W, H = size
    near = [f for f in faces if seg.src_in - 0.5 <= f["t"] <= seg.src_out + 0.5] or faces
    cx = float(np.median([f["cx"] for f in near])) if near else 0.5
    cy = float(np.median([f["cy"] for f in near])) if near else 0.42
    fh = float(np.median([f["fh"] for f in near])) if near else 0.25
    spread = float(np.std([f["cx"] for f in near])) if len(near) > 1 else 0.0
    portrait = W / H <= 9 / 16

    z = base_zoom * seg.zoom
    if spread > 0.05:  # handheld drift: give the face room
        z = min(z, base_zoom)
    z = min(z, max_face / max(fh, 1e-3))
    z = max(z, 1.0)

    def window(zz: float) -> tuple[int, int, int, int]:
        if portrait:
            cw = W / zz
            ch = cw * 16 / 9
        else:
            ch = H / zz
            cw = ch * 9 / 16
        cw, ch = int(cw) // 2 * 2, int(min(ch, H)) // 2 * 2
        x = int(np.clip(cx * W - cw / 2, 0, W - cw)) // 2 * 2
        y = int(np.clip(cy * H - 0.40 * ch, 0, H - ch)) // 2 * 2
        return cw, ch, x, y

    while z > 1.0:
        cw, ch, x, y = window(z)
        fx = (cx * W - x) / cw  # face centre within crop, 0..1
        if 0.25 <= fx <= 0.75:
            break
        z = max(1.0, z - 0.05)
    return window(z)


# --------------------------------------------------------------------------- video

def render_segments(master: Path, segs: list[Segment], faces: list[dict[str, float]], size: tuple[int, int],
                    work: Path, base_zoom: float, progress: Callable[[float], None] | None = None, grade: str = "") -> Path:
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("seg_*.mp4"):
        old.unlink()
    done = [0]

    def one(k: int, s: Segment) -> Path:
        out = work / f"seg_{k:04d}.mp4"
        cw, ch, x, y = crop_for(s, faces, size, base_zoom)
        n = int(round(s.dur * FPS))
        _run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{s.src_in:.4f}", "-i", str(master), "-frames:v", str(n),
              "-vf", f"crop={cw}:{ch}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1" + (f",{grade}" if grade else ""),
              "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS),
              "-video_track_timescale", "15360", str(out)], 600)
        done[0] += 1
        if progress:
            progress(done[0] / len(segs))
        return out

    with ThreadPoolExecutor(max_workers=6) as ex:
        files = list(ex.map(lambda a: one(*a), enumerate(segs)))
    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in files))
    joined = work / "video.mp4"
    _run([config.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    return joined


# --------------------------------------------------------------------------- audio

def assemble_audio(audio: np.ndarray, sr: int, segs: list[Segment], fade_ms: float = 6) -> np.ndarray:
    """Sample-exact cut list with tiny fades so cuts never click; lengths match the video frames."""
    per_frame = sr // FPS
    fade = int(sr * fade_ms / 1000)
    ramp = np.linspace(0, 1, fade, dtype=np.float32) if fade else None
    parts = []
    for s in segs:
        n = int(round(s.dur * FPS)) * per_frame
        a = int(round(s.src_in * sr))
        chunk = np.zeros(n, dtype=np.float32)
        piece = audio[a: a + n]
        chunk[: len(piece)] = piece
        if ramp is not None and n > 2 * fade:
            chunk[:fade] *= ramp
            chunk[-fade:] *= ramp[::-1]
        parts.append(chunk)
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


AUDIO_CHAIN = "highpass=f=80,afftdn=nf=-30:nr=10,acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2"


def master_audio(raw: Path, out: Path) -> dict[str, Any]:
    """Clean up + two-pass loudness normalisation to -14 LUFS / -1.5 dBTP."""
    first = _run([config.FFMPEG, "-hide_banner", "-i", str(raw), "-af",
                  f"{AUDIO_CHAIN},loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], 600)
    txt = first.stderr
    stats = json.loads(txt[txt.rindex("{"): txt.rindex("}") + 1])
    ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
          f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true")
    _run([config.FFMPEG, "-y", "-v", "error", "-i", str(raw), "-af", f"{AUDIO_CHAIN},{ln}", "-ar", str(SR), str(out)], 600)
    return stats


def face_top_out(seg: Segment, faces: list[dict[str, float]], size: tuple[int, int], base_zoom: float) -> int:
    """Top of the speaker's head in output pixels for this segment (where free space above them ends)."""
    W, H = size
    cw, ch, x, y = crop_for(seg, faces, size, base_zoom)
    near = [f for f in faces if seg.src_in - 0.5 <= f["t"] <= seg.src_out + 0.5] or faces
    if not near:
        return int(OUT_H * 0.25)
    cy = float(np.median([f["cy"] for f in near]))
    fh = float(np.median([f["fh"] for f in near]))
    head_top = (cy - fh * 0.62) * H  # detector box starts at the brow; hair sits above it
    return int(max(0, (head_top - y) / ch * OUT_H))


def face_bottom_out(seg: Segment, faces: list[dict[str, float]], size: tuple[int, int], base_zoom: float) -> int:
    """Bottom of the speaker's chin in output pixels."""
    W, H = size
    cw, ch, x, y = crop_for(seg, faces, size, base_zoom)
    near = [f for f in faces if seg.src_in - 0.5 <= f["t"] <= seg.src_out + 0.5] or faces
    if not near:
        return int(OUT_H * 0.6)
    cy = float(np.median([f["cy"] for f in near]))
    fh = float(np.median([f["fh"] for f in near]))
    return int(min(OUT_H, max(0, ((cy + fh * 0.58) * H - y) / ch * OUT_H)))


def typical_chin(segs: list[Segment], faces: list[dict[str, float]], size: tuple[int, int], base_zoom: float) -> int:
    """Chin height across the edit (90th percentile, so close-ups don't get captions on the mouth)."""
    ys = [face_bottom_out(s, faces, size, base_zoom) for s in segs] or [int(OUT_H * 0.6)]
    return int(np.percentile(ys, 90))


CAPTION_TOP = 1250  # captions sit at MarginV 560 → keep graphics above ~1250px


def best_band(seg: Segment, faces: list[dict[str, float]], size: tuple[int, int], base_zoom: float,
              want: int = 460, caption_top: int = CAPTION_TOP) -> dict[str, int]:
    """Free space above the head, or between chin and captions — whichever fits the graphic better."""
    head = face_top_out(seg, faces, size, base_zoom)
    chin = face_bottom_out(seg, faces, size, base_zoom)
    W, H = size
    cw, ch, x, y = crop_for(seg, faces, size, base_zoom)
    near = [f for f in faces if seg.src_in - 0.5 <= f["t"] <= seg.src_out + 0.5] or faces
    if near:
        cy = float(np.median([f["cy"] for f in near]))
        fh = float(np.median([f["fh"] for f in near]))
        eyes = int(((cy - fh * 0.12) * H - y) / ch * OUT_H)
    else:
        eyes = int(OUT_H * 0.35)
    clean = head - 70 - 30
    # selfie footage often leaves no clean space: allow cards over hair/forehead, never the eyes
    top_h = clean if clean >= want else max(clean, eyes - 70 - 70)
    top_band = {"top": 70, "height": int(max(0, top_h))}
    low_top = chin + 40
    low_band = {"top": int(low_top), "height": int(max(0, caption_top - 20 - low_top))}
    if top_band["height"] >= want or top_band["height"] >= low_band["height"]:
        band = top_band
    else:
        band = low_band
    band["height"] = int(min(600, max(240, band["height"])))
    return band


def final_mux(video: Path, audio: Path | None, ass: Path | None, out: Path, overlays: list[dict[str, Any]] | None = None) -> None:
    overlays = overlays or []
    inputs: list[str] = []
    chain: list[str] = []
    last = "0:v"
    for k, o in enumerate(overlays):
        inputs += ["-itsoffset", f"{o['start']:.3f}", "-i", o["file"]]
        nxt = f"g{k}"
        chain.append(f"[{last}][{k + (2 if audio else 1)}:v]overlay=0:{o['band']['top']}:eof_action=pass:format=auto[{nxt}]")
        last = nxt
    if ass:
        chain.append(f"[{last}]ass={ass}:fontsdir={FONTS_DIR}[vout]")
        last = "vout"
    fc = ["-filter_complex", ";".join(chain), "-map", f"[{last}]"] if chain else ["-map", "0:v:0"]
    audio_in = ["-i", str(audio)] if audio else []
    audio_map = ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR)] if audio else ["-an"]
    _run([config.FFMPEG, "-y", "-v", "error", "-i", str(video), *audio_in, *inputs, *fc, *audio_map,
          "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high", "-pix_fmt", "yuv420p",
          "-r", str(FPS), "-movflags", "+faststart", "-shortest", str(out)])


def thumbnail(video: Path, out: Path, at: float = 0.6) -> None:
    _run([config.FFMPEG, "-y", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1",
          "-vf", "scale=540:-2", "-q:v", "3", str(out)], 60)
