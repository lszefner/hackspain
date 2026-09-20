export const RUN_STAGE_LABELS: Record<string, string> = {
  recibida: "Receiving the file",
  extraida: "Extracting invoice fields",
  evaluada: "Evaluating rules",
  revisada: "Contextual review",
  emitida: "Writing the recommendation",
  resuelta: "Resolution",
  pagada: "Payment",
};

export function labelForEtapa(etapa: string): string {
  return RUN_STAGE_LABELS[etapa] ?? etapa;
}

export function stoppedRunError(
  state: string,
  engineBusy: boolean,
  engineError: string | null,
  runError?: string | null,
): string | null {
  // A claimed run can temporarily project as unknown before its input is saved.
  if (state !== "unknown" || engineBusy) return null;
  return engineError || runError || "The engine stopped with an unknown outcome.";
}
