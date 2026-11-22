import { useEffect } from "react";
import { errorMessage, get, post, type Thought } from "../api/client";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";
import { busy, crosspostsOf, siteName, type Crosspost } from "../lib/crosspost";

/** Where a thought was also published, with a retry for failures. While a
 *  copy is being sent the status is re-read every few seconds. */
export function Crossposts({ thought }: { thought: Thought }) {
  const app = useApp();
  const { t } = useI18n();
  const list = crosspostsOf(thought);
  const sending = list.some(busy);

  useEffect(() => {
    if (!sending) return;
    let tries = 0;
    const timer = window.setInterval(async () => {
      tries += 1;
      try {
        const next = await get<Crosspost[]>(`/thoughts/${thought.id}/crossposts`);
        if (!next.some(busy) || tries > 40) {
          window.clearInterval(timer);
          emit({ type: "updated", thought: { ...thought, crossposts: next } });
        }
      } catch {
        if (tries > 40) window.clearInterval(timer);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [sending, thought]);

  if (!list.length) return null;

  async function retry(e: React.MouseEvent, target: string) {
    e.stopPropagation();
    try {
      const next = await post<Crosspost[]>(`/thoughts/${thought.id}/crossposts/${target}`);
      emit({ type: "updated", thought: { ...thought, crossposts: next } });
    } catch (err) {
      app.toast(errorMessage(err));
    }
  }

  return (
    <div className="pk-crossposts" onClick={(e) => e.stopPropagation()}>
      {list.map((c) => { const site = siteName(c.target); return (
        <span key={c.target} className={"pk-crosspost " + c.status}>
          {c.status === "sent" && c.url ? (
            <a href={c.url} target="_blank" rel="noopener noreferrer">{t("cp_sent", { site })} ↗</a>
          ) : busy(c) ? (
            <span>{t("cp_sending", { site })}</span>
          ) : c.status === "sent" ? (
            <span>{t("cp_sent", { site })}</span>
          ) : (
            <>
              <span title={c.error_message ?? undefined}>
                {c.status === "unknown" ? t("cp_unknown", { site }) : t("cp_failed", { site })}
                {c.status === "failed" && c.error_code && <> · {cpReason(t, c, site)}</>}
              </span>
              <button className="pk-link-btn" onClick={(e) => retry(e, c.target)}>{c.status === "unknown" ? t("cp_send_again") : t("cp_retry")}</button>
            </>
          )}
        </span>
      ); })}
    </div>
  );
}

function cpReason(t: ReturnType<typeof useI18n>["t"], c: Crosspost, site: string): string {
  const kind = (c.error_code ?? "").replace(/^(teswirler|makalam)_/, "");
  switch (kind) {
    case "session_expired":
    case "not_connected":
      return t("cp_reconnect");
    case "unavailable":
      return t("cp_unavailable", { site });
    case "too_long":
      return t("e_makalam_too_long");
    default:
      return c.error_message || t("cp_rejected");
  }
}
