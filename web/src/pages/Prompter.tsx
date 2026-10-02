import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { useLoad } from "../util";

/** Full-screen teleprompter. Space = play/pause, ↑/↓ = speed, M = mirror. */
export default function Prompter() {
  const id = Number(useParams().id);
  const { data: idea } = useLoad(() => api.idea(id), [id]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(40); // px per second
  const [size, setSize] = useState(44);
  const [mirror, setMirror] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const last = useRef<number | null>(null);

  useEffect(() => {
    let raf = 0;
    const tick = (t: number) => {
      if (playing && box.current) {
        const dt = last.current === null ? 0 : (t - last.current) / 1000;
        box.current.scrollTop += speed * dt;
      }
      last.current = t;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "ArrowUp") setSpeed((s) => Math.min(200, s + 10));
      else if (e.key === "ArrowDown") setSpeed((s) => Math.max(10, s - 10));
      else if (e.key.toLowerCase() === "m") setMirror((m) => !m);
      else if (e.key === "Home" && box.current) box.current.scrollTop = 0;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const s = idea?.script;
  return (
    <div className="prompter">
      <div className="prompter-bar">
        <Link to={`/ideas/${id}`} className="btn btn-sm btn-ghost">
          ← Back
        </Link>
        <button className="btn btn-sm btn-primary" onClick={() => setPlaying((p) => !p)}>
          {playing ? "Pause" : "Play"} (space)
        </button>
        <button className="btn btn-sm" onClick={() => box.current && (box.current.scrollTop = 0)}>
          Restart
        </button>
        <label className="muted small">
          Speed <input type="range" min={10} max={200} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <label className="muted small">
          Size <input type="range" min={28} max={80} value={size} onChange={(e) => setSize(Number(e.target.value))} />
        </label>
        <button className="btn btn-sm" onClick={() => setMirror((m) => !m)}>
          Mirror {mirror ? "on" : "off"}
        </button>
      </div>
      <div className="prompter-scroll" ref={box} onClick={() => setPlaying((p) => !p)}>
        <div className="prompter-text" style={{ fontSize: size, transform: mirror ? "scaleX(-1)" : undefined }}>
          <div className="prompter-pad" />
          {s ? (
            <>
              <p className="prompter-hook">{s.hook.spoken}</p>
              {s.lines.map((l) => (
                <p key={l.id}>{l.text}</p>
              ))}
              <p className="prompter-end">— end —</p>
            </>
          ) : (
            <p>{idea ? "No script yet." : "Loading…"}</p>
          )}
          <div className="prompter-pad" />
        </div>
      </div>
      <div className="prompter-guide" />
    </div>
  );
}
