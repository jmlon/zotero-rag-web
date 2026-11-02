import * as Popover from "@radix-ui/react-popover";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Source } from "../api/types";

/** `[n]` in an answer: hover or tap for the source, follow its link to Zotero. */
export function CitationChip({ number, source }: { number: number; source?: Source }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // How the last press began: a mouse has already opened the card by hovering, so
  // its click must keep it open; a tap or Enter toggles it.
  const pointer = useRef<string>("");
  const trigger = useRef<HTMLButtonElement>(null);
  if (!source) return <span className="chip chip-unknown">[{number}]</span>;
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        ref={trigger}
        className="chip"
        aria-label={`[${number}] ${source.title}`}
        onPointerEnter={(event) => event.pointerType === "mouse" && setOpen(true)}
        onPointerLeave={(event) => event.pointerType === "mouse" && setOpen(false)}
        onPointerDown={(event) => {
          pointer.current = event.pointerType;
        }}
        onClick={(event) => {
          event.preventDefault(); // Radix would toggle: decide here instead
          // Read now: React runs the updater later, after the reset below.
          const byMouse = pointer.current === "mouse";
          pointer.current = "";
          setOpen((current) => (byMouse ? true : !current));
        }}
      >
        {number}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="citation-card"
          side="top"
          sideOffset={6}
          collisionPadding={16}
          onPointerEnter={() => setOpen(true)}
          onPointerLeave={(event) => event.pointerType === "mouse" && setOpen(false)}
          // A press on the chip is not "outside": the chip's own click decides.
          onInteractOutside={(event) => {
            if (trigger.current?.contains(event.target as Node)) event.preventDefault();
          }}
        >
          <p className="citation-title">{source.title}</p>
          <p className="citation-meta">
            {[source.authors, source.year, source.locator].filter(Boolean).join(" · ")}
          </p>
          {source.zotero_url && (
            <a href={source.zotero_url} target="_blank" rel="noopener noreferrer">
              {t("answer.openInZotero")} ↗
            </a>
          )}
          <Popover.Arrow className="citation-arrow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
