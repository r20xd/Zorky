// Затемнённое окно поверх экрана: плавно появляется и уходит, Escape закрывает, фокус возвращается назад.
import { useEffect, useRef, type ReactNode } from "react";
import { usePresence, useReturnFocus } from "./hooks";

type Props = {
  open: boolean;
  onClose: () => void;
  label: string;
  children: ReactNode;
};

export default function Lightbox({ open, onClose, label, children }: Props) {
  const { mounted, state } = usePresence(open, 200);
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useReturnFocus(open);
  useEffect(() => {
    if (!open) return;
    box.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  if (!mounted) return null;
  return (
    <div
      ref={box}
      className={`lightbox${state === "closing" ? " closing" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      tabIndex={-1}
      onClick={onClose}
    >
      {children}
    </div>
  );
}
