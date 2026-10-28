import type { ReactNode } from "react";
import { useApp } from "../app/context";
import { I, Icon } from "../lib/icons";

export function Header({ title, sub, back, right, tabs, active, onTab, mark }: {
  title: string;
  sub?: string;
  back?: boolean;
  right?: ReactNode;
  tabs?: [string, string][];
  active?: string;
  onTab?: (k: string) => void;
  /** Show the P mark next to the title (mobile home). */
  mark?: boolean;
}) {
  const app = useApp();
  return (
    <header className="pk-header">
      <div className="pk-header-row">
        {back && (
          <button className="pk-square-btn" onClick={app.back} aria-label="Back">
            <Icon d={I.back} size="sm" />
          </button>
        )}
        {mark && app.isMobile && <span className="pk-mark" style={{ width: 28, height: 28 }}>P</span>}
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 className="pk-title">{title}</h1>
          {sub && <div className="pk-sub">{sub}</div>}
        </div>
        {right}
      </div>
      {tabs && (
        <div role="tablist" className="pk-tabs">
          {tabs.map(([k, label]) => (
            <button key={k} role="tab" className="pk-tab" aria-selected={active === k} onClick={() => onTab?.(k)}>
              {label}
            </button>
          ))}
        </div>
      )}
    </header>
  );
}
