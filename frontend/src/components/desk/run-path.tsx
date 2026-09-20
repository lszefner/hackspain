"use client";

import { useEffect, useState } from "react";

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
  const hotIndex = Math.max(
    0,
    stages.findIndex((s) => s.state === "hot"),
  );
  const activeIndex =
    status === "done"
      ? stages.length - 1
      : status === "failed"
        ? Math.max(
            0,
            stages.findIndex((s) => s.state !== "done"),
          )
        : hotIndex >= 0
          ? hotIndex
          : stages.findIndex((s) => s.state === "done") + 1;

  const safeIndex = Math.min(
    stages.length - 1,
    Math.max(0, activeIndex < 0 ? 0 : activeIndex),
  );
  const active = stages[safeIndex];
  const [enterKey, setEnterKey] = useState(active?.id ?? "0");

  useEffect(() => {
    setEnterKey(`${active?.id ?? safeIndex}-${active?.state ?? "pending"}`);
  }, [active?.id, active?.state, safeIndex]);

  const progress =
    stages.length <= 1
      ? status === "done"
        ? 100
        : 12
      : Math.round(
          ((stages.filter((s) => s.state === "done").length +
            (status === "running" && active?.state === "hot" ? 0.45 : 0)) /
            stages.length) *
            100,
        );

  return (
    <div className={`run-path ${status}`} role="status">
      <div className="run-path-head">
        <p className="run-path-title">{active?.label ?? title}</p>
        <span className="run-path-meta">
          {safeIndex + 1}/{stages.length}
        </span>
      </div>

      <div className="run-path-stage" key={enterKey}>
        <p className="run-path-copy">{title}</p>
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

      <div className="run-path-rail" aria-hidden>
        <div className="run-path-track">
          <i style={{ width: `${Math.min(100, Math.max(8, progress))}%` }} />
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
              <span className="node-lbl">{stage.label}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
