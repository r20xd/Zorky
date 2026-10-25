import type { ReactNode } from "react";
import type { Severity } from "../../domain/types";

function Icon({ size = 18, strokeWidth = 2.2, children }: { size?: number; strokeWidth?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** Критичность различается формой: восьмиугольник → треугольник → ромб → круг (не только цветом). */
export function SeverityIcon({ severity, size }: { severity: Severity; size?: number }) {
  switch (severity) {
    case "critical":
      return (
        <Icon size={size}>
          <path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z" />
          <path d="M12 7v6" />
          <path d="M12 17h.01" />
        </Icon>
      );
    case "high":
      return (
        <Icon size={size}>
          <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </Icon>
      );
    case "medium":
      return (
        <Icon size={size}>
          <path d="M12 2 22 12 12 22 2 12z" />
          <path d="M12 8v4" />
          <path d="M12 16h.01" />
        </Icon>
      );
    case "low":
      return (
        <Icon size={size}>
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4" />
          <path d="M12 8h.01" />
        </Icon>
      );
  }
}

export function ShieldCheckIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size} strokeWidth={2.4}>
      <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z" />
      <path d="m9 12 2 2 4-4" />
    </Icon>
  );
}

export function LogoutIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size} strokeWidth={2}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </Icon>
  );
}

export function CheckIcon({ size = 24 }: { size?: number }) {
  return (
    <Icon size={size} strokeWidth={3}>
      <path d="M20 6 9 17l-5-5" />
    </Icon>
  );
}

export function ChevronDownIcon({ size = 16 }: { size?: number }) {
  return (
    <Icon size={size} strokeWidth={2}>
      <path d="m6 9 6 6 6-6" />
    </Icon>
  );
}

export function BellIcon({ size = 18, off = false }: { size?: number; off?: boolean }) {
  return (
    <Icon size={size} strokeWidth={2}>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
      {off && <path d="m3 3 18 18" />}
    </Icon>
  );
}
