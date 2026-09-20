"use client";

type Kind = "pdf" | "zip";

/** Clean document mark for the attach tray and message chips. */
export function FileIcon({ kind = "pdf" }: { kind?: Kind }) {
  if (kind === "zip") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
        <path
          d="M3.2 4.2h3.1l1.1 1.3h5.4A1.1 1.1 0 0 1 13.9 6.6v5.6a1.1 1.1 0 0 1-1.1 1.1H3.2A1.1 1.1 0 0 1 2.1 12.2V5.3A1.1 1.1 0 0 1 3.2 4.2z"
          stroke="currentColor"
          strokeWidth="1.35"
          strokeLinejoin="round"
        />
        <path
          d="M6.4 7.6h3.2M6.4 9.7h2.2"
          stroke="currentColor"
          strokeWidth="1.25"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M4.2 2.4h5.1L12 5.1v8a.7.7 0 0 1-.7.7H4.2a.7.7 0 0 1-.7-.7V3.1a.7.7 0 0 1 .7-.7z"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinejoin="round"
      />
      <path
        d="M9.2 2.4V5h2.7"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinejoin="round"
      />
      <path
        d="M5.6 8.2h4.8M5.6 10.4h3.4"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </svg>
  );
}
