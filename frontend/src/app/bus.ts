import { useEffect, useRef } from "react";
import type { Thought } from "../api/client";

/** Local change notifications, so every visible list reflects a mutation
 *  without refetching (publish, edit, bookmark, delete, restore). */
export type ThoughtEvent =
  | { type: "created"; thought: Thought }
  | { type: "updated"; thought: Thought }
  | { type: "deleted"; id: string }
  | { type: "restored"; thought: Thought };

const bus = new EventTarget();

export function emit(e: ThoughtEvent) {
  bus.dispatchEvent(new CustomEvent("thought", { detail: e }));
}

export function useThoughtEvents(handler: (e: ThoughtEvent) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const fn = (ev: Event) => ref.current((ev as CustomEvent<ThoughtEvent>).detail);
    bus.addEventListener("thought", fn);
    return () => bus.removeEventListener("thought", fn);
  }, []);
}
