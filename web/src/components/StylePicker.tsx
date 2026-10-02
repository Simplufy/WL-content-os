import type { EditStyle } from "../api";

export function StyleThumb({ s, size = 150 }: { s: EditStyle; size?: number }) {
  if (s.gif) return <img className="style-gif" src={s.gif} alt={`${s.name} preview`} loading="lazy" style={{ width: size }} />;
  return (
    <div className="style-gif style-gif-empty" style={{ width: size }}>
      {s.preview_status === "failed" ? "Preview failed" : <><span className="spinner" /> Rendering preview…</>}
    </div>
  );
}

export default function StylePicker({ styles, value, onPick }: { styles: EditStyle[]; value: number | null | undefined; onPick: (id: number) => void }) {
  return (
    <div className="style-picker">
      {styles.map((s) => (
        <button key={s.id} type="button" className={`style-pick ${value === s.id ? "on" : ""}`} onClick={() => onPick(s.id)} title={s.description || ""}>
          <StyleThumb s={s} size={130} />
          <div className="style-pick-name">{s.name}</div>
          {s.source === "builtin" && <div className="muted small">house style</div>}
        </button>
      ))}
    </div>
  );
}
