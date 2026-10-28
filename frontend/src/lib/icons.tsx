// Lucide-style paths, as drawn in the design (square caps, mitred joins).
export const I = {
  home: "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M9 22V12h6v10",
  user: "M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z M20 21a8 8 0 0 0-16 0",
  bookmark: "M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z",
  hash: "M4 9h16 M4 15h16 M10 3L8 21 M16 3l-2 18",
  folder: "M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z",
  history: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8 M3 3v5h5 M12 7v5l4 2",
  spark: "M12 3l-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z",
  calendar: "M3 4h18v18H3z M16 2v4 M8 2v4 M3 10h18",
  settings: "M21 4h-7 M10 4H3 M21 12h-9 M8 12H3 M21 20h-5 M12 20H3 M14 2v4 M8 10v4 M16 18v4",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.3-4.3",
  plus: "M5 12h14 M12 5v14",
  lock: "M3 11h18v11H3z M7 11V7a5 5 0 0 1 10 0v4",
  moon: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M6.3 17.7l-1.4 1.4 M19.1 4.9l-1.4 1.4",
  back: "M12 19l-7-7 7-7 M19 12H5",
  left: "M15 18l-6-6 6-6",
  right: "M9 18l6-6-6-6",
  arrow: "M5 12h14 M12 5l7 7-7 7",
  x: "M18 6L6 18 M6 6l12 12",
  reply: "M15 10l5 5-5 5 M4 4v7a4 4 0 0 0 4 4h12",
  link: "M9 17H7A5 5 0 0 1 7 7h2 M15 7h2a5 5 0 1 1 0 10h-2 M8 12h8",
  chain: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.8 1.7 M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
  dots: "M5 12h.01 M12 12h.01 M19 12h.01",
  image: "M3 3h18v18H3z M9 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M21 15l-3.1-3.1a2 2 0 0 0-2.8 0L6 21",
  video: "M22 8l-6 4 6 4V8z M2 6h14v12H2z",
  mic: "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z M19 10v2a7 7 0 0 1-14 0v-2 M12 19v3",
  clip: "M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.6A4 4 0 1 1 18 8.8l-8.6 8.6a2 2 0 0 1-2.8-2.8l8.5-8.5",
  shuffle: "M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22 M18 2l4 4-4 4 M2 6h1.9c1.5 0 2.9.9 3.6 2.2 M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-.5-.8 M18 14l4 4-4 4",
  offline: "M12 20h.01 M8.5 16.4a5 5 0 0 1 7 0 M2 8.8a15 15 0 0 1 4.2-2.6 M10.7 5c4-.4 8.1.9 11.3 3.8 M16.9 11.3a10 10 0 0 1 2.2 1.7 M5 13a10 10 0 0 1 5.2-2.8 M2 2l20 20",
  play: "M6 3l14 9-14 9z",
  pause: "M6 4h4v16H6z M14 4h4v16h-4z",
} as const;

export function Icon({ d, size, className = "", fill }: { d: string; size?: "sm" | "xs"; className?: string; fill?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={`pk-icon ${size ? "pk-icon-" + size : ""} ${fill ? "fill" : ""} ${className}`}>
      <path d={d} />
    </svg>
  );
}

export function SolidIcon({ d, px = 14 }: { d: string; px?: number }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" style={{ width: px, height: px, fill: "currentColor" }}>
      <path d={d} />
    </svg>
  );
}
