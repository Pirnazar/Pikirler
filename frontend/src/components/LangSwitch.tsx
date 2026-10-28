import { LANGUAGES, useI18n } from "../i18n";

/** Türkmen / Русский / English segmented switch. */
export function LangSwitch({ large, inverted }: { large?: boolean; inverted?: boolean }) {
  const { lang, setLang, t } = useI18n();
  return (
    <div role="group" aria-label={t("language")} className={"pk-seg" + (large ? " lg" : "") + (inverted ? " inverted" : "")}>
      {LANGUAGES.map(([code, label]) => (
        <button key={code} lang={code} aria-pressed={lang === code} onClick={() => setLang(code)}>{label}</button>
      ))}
    </div>
  );
}
