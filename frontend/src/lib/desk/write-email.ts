import { fold } from "./fold";
import type { OutreachDraft } from "./outreach";

/** A demo tool: deliberately has no transport or persistence. */
export function writeEmail(draft: OutreachDraft, recipient?: string) {
  const to = (recipient || draft.to).trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)) {
    return { tool: "write_email" as const, status: "recipient_required" as const, demo: true as const };
  }
  return {
    tool: "write_email" as const, status: "written" as const,
    demo: true as const, sent: false as const, draft: { ...draft, to },
  };
}

/** Only consume a direct reply to the current email proposal. */
export function emailReply(text: string) {
  const t = fold(text).trim().replace(/[.!?]+$/, "");
  if (/^(no|no thanks|not now|cancel|no gracias|ahora no|cancelar)$/.test(t)) return { action: "cancel" as const };
  const recipient = text.match(/[^\s<>@]+@[^\s<>@]+\.[a-z]{2,}/i)?.[0];
  if (recipient && /^(?:to |a |write (?:it )?to |send (?:it )?to |escribe a )?[^\s<>@]+@[^\s<>@]+\.[a-z]{2,}[.!]?$/i.test(text.trim())) {
    return { action: "write" as const, recipient };
  }
  if (/^(yes|yes please|sure|go ahead|ok|okay|si|si por favor|vale|adelante|write (it|the email|the draft)|draft (it|the email)|send (it|the email)|escribe(lo| el correo)|redacta(lo| el correo))(,? please| por favor)?$/.test(t)) return { action: "write" as const };
  return null;
}
