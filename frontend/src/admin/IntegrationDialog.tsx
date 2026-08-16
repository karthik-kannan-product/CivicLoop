import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

export function IntegrationDialog({ title, description, busy, onClose, children }: { title: string; description: string; busy: boolean; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => { dialog.current?.querySelector<HTMLElement>("input, select, button")?.focus(); }, []);
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); return; }
    if (event.key !== "Tab") return;
    const items = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled)") ?? []);
    if (!items.length) return;
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
    if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
  }
  return <div className="security-dialog-backdrop"><div ref={dialog} aria-describedby="integration-dialog-description" aria-labelledby="integration-dialog-title" aria-modal="true" className="security-dialog" onKeyDown={keys} role="dialog"><h2 id="integration-dialog-title">{title}</h2><p id="integration-dialog-description">{description}</p>{children}</div></div>;
}
