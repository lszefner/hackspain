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
