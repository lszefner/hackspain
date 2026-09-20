"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Liquid } from "liquid-gooey";

export type AttachKind = "pdf" | "image" | "zip";

type Props = {
  disabled?: boolean;
  hasAttachment?: boolean;
  onPick: (kind: AttachKind, files: FileList | null) => void;
};

const ACCEPT: Record<AttachKind, string> = {
  pdf: "application/pdf,.pdf",
  image: "image/png,image/jpeg,image/webp,image/heic,.png,.jpg,.jpeg,.webp",
  zip: "application/zip,.zip,application/x-zip-compressed",
};

const ANCHOR = {
  position: "absolute" as const,
  left: 0,
  bottom: 0,
};

const DESK_SPRING = { stiffness: 280, damping: 22, mass: 0.9 } as const;

/** Fan into the composer — discrete circles with clear gaps. */
const OPEN = {
  pdf: { x: 52, y: 0 },
  image: { x: 104, y: 0 },
  zip: { x: 156, y: 0 },
} as const;

export function AttachMenu({ disabled, hasAttachment, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const pdfRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  const uid = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  function pick(kind: AttachKind) {
    setOpen(false);
    const ref =
      kind === "pdf" ? pdfRef : kind === "image" ? imageRef : zipRef;
    window.setTimeout(() => ref.current?.click(), 120);
  }

  return (
    <div
      ref={rootRef}
      className={`attach-slot ${open ? "open" : ""} ${hasAttachment ? "has" : ""}`}
    >
      <input
        ref={pdfRef}
        id={`${uid}-pdf`}
        type="file"
        accept={ACCEPT.pdf}
        hidden
        onChange={(e) => {
          onPick("pdf", e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={imageRef}
        id={`${uid}-image`}
        type="file"
        accept={ACCEPT.image}
        hidden
        onChange={(e) => {
          onPick("image", e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={zipRef}
        id={`${uid}-zip`}
        type="file"
        accept={ACCEPT.zip}
        hidden
        onChange={(e) => {
          onPick("zip", e.target.files);
          e.target.value = "";
        }}
      />

      <div className="attach-liquid-wrap">
        <Liquid
          className="attach-liquid"
          blur={0}
          contrast={18}
          fill="transparent"
          shadow="none"
          filterPadding={0}
        >
          <Liquid.Item
            style={ANCHOR}
            x={open ? OPEN.pdf.x : 0}
            y={open ? OPEN.pdf.y : 0}
            transition={DESK_SPRING}
          >
            <button
              type="button"
              className="attach-orb"
              disabled={disabled || !open}
              tabIndex={open ? 0 : -1}
              title="One PDF"
              aria-label="Attach one PDF"
              onClick={() => pick("pdf")}
            >
              <DocIcon />
            </button>
          </Liquid.Item>
          <Liquid.Item
            style={ANCHOR}
            x={open ? OPEN.image.x : 0}
            y={open ? OPEN.image.y : 0}
            transition={DESK_SPRING}
            delay={36}
          >
            <button
              type="button"
              className="attach-orb"
              disabled={disabled || !open}
              tabIndex={open ? 0 : -1}
              title="Photo or scan"
              aria-label="Attach a photo or scan"
              onClick={() => pick("image")}
            >
              <ImageIcon />
            </button>
          </Liquid.Item>
          <Liquid.Item
            style={ANCHOR}
            x={open ? OPEN.zip.x : 0}
            y={open ? OPEN.zip.y : 0}
            transition={DESK_SPRING}
            delay={72}
          >
            <button
              type="button"
              className="attach-orb"
              disabled={disabled || !open}
              tabIndex={open ? 0 : -1}
              title="Zip of PDFs"
              aria-label="Attach a zip of PDFs"
              onClick={() => pick("zip")}
            >
              <FolderIcon />
            </button>
          </Liquid.Item>
          <Liquid.Item style={ANCHOR} transition={DESK_SPRING}>
            <button
              type="button"
              className={`attach-orb attach-toggle ${open ? "open" : ""}`}
              disabled={disabled}
              title={open ? "Close" : "Attach a document"}
              aria-label={open ? "Close attach menu" : "Attach a document"}
              aria-expanded={open}
              aria-haspopup="menu"
              onClick={() => setOpen((v) => !v)}
            >
              <span className="attach-ico" data-show={!open || undefined}>
                <ClipIcon />
              </span>
              <span className="attach-ico" data-show={open || undefined}>
                <CloseIcon />
              </span>
            </button>
          </Liquid.Item>
        </Liquid>
      </div>
    </div>
  );
}

function ClipIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M6.2 4.1h5.6c.9 0 1.65.72 1.65 1.6v8.3c0 .88-.75 1.6-1.65 1.6H6.2c-.9 0-1.65-.72-1.65-1.6V5.7c0-.88.75-1.6 1.65-1.6z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M7.15 4.1V3.2A1.85 1.85 0 0 1 9 1.4c.98 0 1.8.77 1.85 1.75V4.1"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M6.9 8.15h4.2M6.9 10.55h4.2M6.9 12.95h2.6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
      <path
        d="M3.5 3.5l7 7M10.5 3.5l-7 7"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function DocIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M4.5 2.5h5.2L12 4.8V13a.5.5 0 0 1-.5.5h-7A.5.5 0 0 1 4 13V3a.5.5 0 0 1 .5-.5z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M9.5 2.5V5H12"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M6 8h4M6 10.5h3"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ImageIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <rect
        x="2.5"
        y="3.5"
        width="11"
        height="9"
        rx="1.2"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <circle cx="5.8" cy="6.5" r="1" fill="currentColor" />
      <path
        d="M2.8 11.2l3.2-3.1 2.1 2 2.4-2.6 2.7 3.7"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M2.5 5.2V4.2A1.2 1.2 0 0 1 3.7 3h2.3l1.2 1.4h5.1A1.2 1.2 0 0 1 13.5 5.6v5.7A1.2 1.2 0 0 1 12.3 12.5H3.7A1.2 1.2 0 0 1 2.5 11.3V5.2z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}
