"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { OutreachDraft } from "@/lib/desk/outreach";

export function EmailDraftDialog({
  open,
  draft,
  onOpenChange,
  onSend,
}: {
  open: boolean;
  draft: OutreachDraft | null;
  onOpenChange: (open: boolean) => void;
  onSend: (draft: OutreachDraft) => void;
}) {
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  useEffect(() => {
    if (!draft) return;
    setTo(draft.to);
    setSubject(draft.subject);
    setBody(draft.body);
  }, [draft]);

  if (!draft) return null;

  const mailto = (() => {
    const params = new URLSearchParams();
    if (subject) params.set("subject", subject);
    if (body) params.set("body", body);
    const q = params.toString();
    return `mailto:${encodeURIComponent(to || "")}${q ? `?${q}` : ""}`;
  })();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="email-draft-dialog max-w-xl border-[var(--line)] bg-[var(--panel)] text-[var(--ink)] sm:rounded-xl">
        <DialogHeader>
          <DialogTitle className="text-[15px]">Supplier email draft</DialogTitle>
          <DialogDescription className="text-[12.5px] text-[var(--mut)]">
            {draft.intent_label} · {draft.file_id}. Demo send only — nothing
            leaves over SMTP.
          </DialogDescription>
        </DialogHeader>
        <div className="email-draft-fields">
          <label>
            <span>To</span>
            <input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="supplier@example.com"
              autoComplete="off"
            />
          </label>
          <label>
            <span>Subject</span>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            <span>Body</span>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={10}
            />
          </label>
          <p className="email-draft-why">{draft.why}</p>
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <a className="btn quiet" href={mailto}>
            Open in mail app
          </a>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!to.trim() || !subject.trim() || !body.trim()}
              onClick={() =>
                onSend({
                  ...draft,
                  to: to.trim(),
                  subject: subject.trim(),
                  body: body.trim(),
                })
              }
            >
              Send
            </button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
