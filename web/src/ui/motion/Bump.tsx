import { useRef, type HTMLAttributes } from "react";

/**
 * Значок, который подпрыгивает, когда меняется value (повторы, счётчик на вкладке).
 * При первом показе — тихо: анимация только на изменение.
 */
export default function Bump({ value, className = "", ...rest }: { value: unknown } & HTMLAttributes<HTMLSpanElement>) {
  const seen = useRef({ value, changes: 0 });
  if (seen.current.value !== value) seen.current = { value, changes: seen.current.changes + 1 };
  const { changes } = seen.current;
  return <span key={changes} className={changes > 0 ? `${className} fx-pop`.trim() : className} {...rest} />;
}
