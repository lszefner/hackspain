"use client";

type Props = {
  progress: number;
  size?: number;
  stroke?: number;
  label?: string;
};

/** Counter-style progress ring — Jitter Counter: Progress Ring, desk palette. */
export function ProgressRing({
  progress,
  size = 52,
  stroke = 3.25,
  label,
}: Props) {
  const pct = Math.max(0, Math.min(100, Math.round(progress)));
  const r = (size - stroke) / 2 - 1;
  const c = 2 * Math.PI * r;
  const offset = c - (pct / 100) * c;
  const mid = size / 2;

  return (
    <span
      className="prog-ring"
      style={{ width: size, height: size }}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "progressbar" : undefined}
      aria-valuenow={label ? pct : undefined}
      aria-valuemin={label ? 0 : undefined}
      aria-valuemax={label ? 100 : undefined}
    >
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        <circle
          className="prog-ring-track"
          cx={mid}
          cy={mid}
          r={r}
          fill="none"
          strokeWidth={stroke}
        />
        <circle
          className="prog-ring-prog"
          cx={mid}
          cy={mid}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${mid} ${mid})`}
        />
      </svg>
      <span className="prog-ring-pct">{pct}</span>
    </span>
  );
}
