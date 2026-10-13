import { useState } from "react";
import type { Tally } from "../types";

const BINS = [
  { min: 0, label: "0–19%" },
  { min: 20, label: "20–39%" },
  { min: 40, label: "40–59%" },
  { min: 60, label: "60–79%" },
  { min: 80, label: "80–100%" },
];

function bin(rate: number): number {
  let b = 0;
  BINS.forEach((x, i) => {
    if (rate * 100 >= x.min) b = i;
  });
  return b + 1;
}

export function HeatmapLegend() {
  return (
    <div className="hm-legend" aria-label="pass rate scale">
      <span>pass rate</span>
      {BINS.map((b, i) => (
        <span className="sw" key={b.label}>
          <i style={{ background: `var(--seq-${i + 1})` }} />
          {b.label}
        </span>
      ))}
    </div>
  );
}

interface Props {
  rows: string[];
  cols: string[];
  cell: (row: string, col: string) => Tally | undefined;
  /** extra tooltip line, e.g. the change against the no-fault row */
  note?: (row: string, col: string) => string | null;
  selected?: [string, string] | null;
  onSelect?: (row: string, col: string) => void;
  rowHeader?: string;
}

/** Pass-rate grid. Every cell prints its value; color is a second channel, never the only one. */
export function Heatmap({ rows, cols, cell, note, selected, onSelect, rowHeader }: Props) {
  const [tip, setTip] = useState<{ x: number; y: number; row: string; col: string } | null>(null);

  function show(e: React.SyntheticEvent<HTMLButtonElement>, row: string, col: string) {
    const r = e.currentTarget.getBoundingClientRect();
    setTip({ x: r.left + r.width / 2, y: r.top, row, col });
  }

  const tipCell = tip ? cell(tip.row, tip.col) : undefined;
  const tipNote = tip && note ? note(tip.row, tip.col) : null;

  return (
    <div className="hm-scroll" onScroll={() => setTip(null)}>
    <div
      className="heatmap"
      style={{ gridTemplateColumns: `max-content repeat(${cols.length}, minmax(64px, 104px))` }}
      role="grid"
      onMouseLeave={() => setTip(null)}
    >
      <div className="hm-col" style={{ textAlign: "left", paddingLeft: 0 }}>
        {rowHeader}
      </div>
      {cols.map((c) => (
        <div className="hm-col" key={c} role="columnheader">
          {c}
        </div>
      ))}
      {rows.map((r) => (
        <div key={r} style={{ display: "contents" }} role="row">
          <div className="hm-row" role="rowheader">
            {r}
          </div>
          {cols.map((c) => {
            const t = cell(r, c);
            if (!t || !t.n)
              return (
                <button key={c} className="hm-cell empty" disabled aria-label={`${r}, ${c}: no trials`}>
                  –
                </button>
              );
            const rate = t.passes / t.n;
            const isSel = selected && selected[0] === r && selected[1] === c;
            return (
              <button
                key={c}
                className={`hm-cell b${bin(rate)}${isSel ? " selected" : ""}`}
                aria-label={`${r}, ${c}: ${Math.round(rate * 100)}% pass, ${t.passes} of ${t.n}`}
                aria-pressed={!!isSel}
                onMouseEnter={(e) => show(e, r, c)}
                onFocus={(e) => show(e, r, c)}
                onBlur={() => setTip(null)}
                onClick={() => onSelect?.(r, c)}
              >
                <span className="v">{Math.round(rate * 100)}%</span>
                <span className="n">
                  {t.passes}/{t.n}
                </span>
              </button>
            );
          })}
        </div>
      ))}
      {tip && tipCell && (
        <div className="tooltip" style={{ left: tip.x, top: tip.y - 6, transform: "translate(-50%, -100%)" }}>
          <strong>
            {Math.round((100 * tipCell.passes) / tipCell.n)}% pass · {tipCell.passes} of {tipCell.n}
          </strong>
          <span className="secondary">
            {tip.row} × {tip.col}
          </span>
          {tipNote && <div className="secondary">{tipNote}</div>}
        </div>
      )}
    </div>
    </div>
  );
}
