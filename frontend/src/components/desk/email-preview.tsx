export function EmailPreview({ to, subject, body }: {
  to: string;
  subject: string;
  body: string;
}) {
  return (
    <article className="email-preview" aria-label="Sent email">
      <dl className="email-preview-fields">
        <div><dt>To</dt><dd>{to}</dd></div>
        <div><dt>Subject</dt><dd className="email-preview-subject">{subject}</dd></div>
      </dl>
      <section className="email-preview-content" aria-label="Email body">
        <div className="email-preview-label">Body</div>
        <div className="email-preview-body">{body}</div>
      </section>
    </article>
  );
}
