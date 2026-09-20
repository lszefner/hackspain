"use client";

import { ThinkingOrb, type OrbState } from "thinking-orbs";

function stateForLabel(label: string): OrbState {
  const l = label.toLowerCase();
  if (
    l.includes("check") ||
    l.includes("read") ||
    l.includes("search") ||
    l.includes("dig") ||
    l.includes("receiv") ||
    l.includes("list") ||
    l.includes("build")
  ) {
    return "searching";
  }
  if (
    l.includes("rule") ||
    l.includes("evaluat") ||
    l.includes("argu") ||
    l.includes("judg")
  ) {
    return "solving";
  }
  if (l.includes("writ") || l.includes("draft") || l.includes("compos")) {
    return "composing";
  }
  if (l.includes("connect") || l.includes("send") || l.includes("engine")) {
    return "connecting";
  }
  return "working";
}

/** Agent waiting state — Thinking Orbs from libraries.dev */
export function ThinkPill({ label }: { label: string }) {
  return (
    <span className="think">
      <ThinkingOrb
        state={stateForLabel(label)}
        size={20}
        theme="light"
        aria-label={label}
      />
      <span className="lbl">{label}</span>
    </span>
  );
}
