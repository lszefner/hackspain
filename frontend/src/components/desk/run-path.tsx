"use client";

import type { CSSProperties } from "react";

export type PathStage = {
  id: string;
  label: string;
  state: "pending" | "hot" | "done";
  subtasks?: { id: string; label: string; state: "pending" | "hot" | "done" }[];
};

type Props = {
  title: string;
  status: "running" | "done" | "failed";
  stages: PathStage[];
};

export function RunPath({ title, status, stages }: Props) {
  const hotIndex = stages.findIndex((s) => s.state === "hot");
  const nextIndex = stages.findIndex((s) => s.state !== "done");
  const safeIndex = Math.max(0, Math.min(stages.length - 1,
    status === "done" || nextIndex === -1 ? stages.length - 1
      : hotIndex >= 0 ? hotIndex : nextIndex));
  const active = stages[safeIndex];
  const progress = stages.length <= 1 ? (status === "done" ? 100 : 0)
    : safeIndex / (stages.length - 1) * 100;
  const heading = status === "running" ? active?.label || title : title;
  const detail = title !== heading ? title : null;

  return (
    <div className={`run-path ${status}`} role="status">
      <div className="run-path-head">
        <p className="run-path-title">{heading}</p>
        <span className="run-path-meta">
          {stages.length ? `${safeIndex + 1} of ${stages.length}` : ""}
        </span>
      </div>

      <div className="run-path-stage">
        {detail ? <p className="run-path-copy">{detail}</p> : null}
        {active?.subtasks?.length ? (
          <ul className="run-path-subs">
            {active.subtasks.map((sub) => (
              <li
                key={sub.id}
                className={
                  sub.state === "done"
                    ? "is-done"
                    : sub.state === "hot"
                      ? "is-hot"
                      : "is-pending"
                }
              >
                <span className="mark" aria-hidden />
                {sub.label}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="run-path-rail" aria-hidden style={{ "--stage-count": Math.max(1, stages.length) } as CSSProperties}>
        <div className="run-path-track">
          <i style={{ width: `${progress}%` }} />
        </div>
        <ol className="run-path-nodes">
          {stages.map((stage, i) => (
            <li
              key={stage.id}
              className={
                stage.state === "done"
                  ? "is-done"
                  : stage.state === "hot" || i === safeIndex
                    ? "is-hot"
                    : "is-pending"
              }
              title={stage.label}
            >
              <span className="node" />
              <span className="node-lbl">{({ recibida: "Receive", extraida: "Extract", evaluada: "Evaluate", emitida: "Recommend", "fallback-0": "Receive", "fallback-1": "Extract", "fallback-2": "Evaluate", "fallback-3": "Recommend" } as Record<string, string>)[stage.id] || stage.label}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
