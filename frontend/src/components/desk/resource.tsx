"use client";
import useSWR from "swr";
import { ThinkingOrb } from "thinking-orbs";
import { Button } from "@/components/ui/button";
async function fetcher(url: string) {
  const response = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(25000),
  });
  if (response.status === 401)
    throw new Error("Your session expired. Sign in again to continue.");
  if (!response.ok)
    throw new Error(
      `Could not load saved records (${response.status}). Check the API connection and retry.`,
    );
  return response.json();
}
export function useResource<T>(url: string | null) {
  return useSWR<T>(url, fetcher, {
    revalidateOnFocus: false,
    revalidateOnReconnect: true,
    dedupingInterval: 5000,
    shouldRetryOnError: false,
  });
}
export function Loading({
  label = "Loading recorded invoices…",
}: {
  label?: string;
}) {
  return (
    <div className="loading" role="status">
      <span className="orb-loader" aria-hidden>
        <ThinkingOrb size={20} theme="light" />
      </span>
      {label}
    </div>
  );
}
export function Failure({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="empty-note" role="alert">
      {error.message}
      <Button className="btn" onClick={retry}>
        Retry
      </Button>
      {error.message.includes("session") ? <a href="/access">Sign in</a> : null}
    </div>
  );
}
export function Pages({
  page,
  total,
  limit,
  onChange,
  label = "Page",
}: {
  page: number;
  total: number;
  limit: number;
  onChange: (page: number) => void;
  label?: string;
}) {
  return (
    <nav aria-label={`${label} pagination`} className="live-pages">
      <Button
        className="btn"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
      >
        Previous
      </Button>
      <span>
        {label} {page} of {Math.max(1, Math.ceil(total / limit))} · {total}{" "}
        records
      </span>
      <Button
        className="btn"
        disabled={page * limit >= total}
        onClick={() => onChange(page + 1)}
      >
        Next
      </Button>
    </nav>
  );
}
export function Json({ value }: { value: unknown }) {
  return (
    <pre className="audit-json">
      {JSON.stringify(value, null, 2) ?? "Not recorded"}
    </pre>
  );
}
