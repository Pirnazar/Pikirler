import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { en, type Dict, type Plural } from "./en";
import { ru } from "./ru";
import { tk } from "./tk";

export type Lang = "tk" | "ru" | "en";
export const LANGUAGES: [Lang, string][] = [["tk", "Türkmen"], ["ru", "Русский"], ["en", "English"]];
const DICTS: Record<Lang, Dict> = { en, ru, tk };
const LOCALES: Record<Lang, string> = { tk: "tk-TM", ru: "ru-RU", en: "en-US" };
const KEY = "pikirler.lang";

type StrKey = { [K in keyof Dict]: Dict[K] extends string ? K : never }[keyof Dict];
type PluralKey = { [K in keyof Dict]: Dict[K] extends Plural ? K : never }[keyof Dict];
type Vars = Record<string, string | number>;

function detect(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "tk" || saved === "ru" || saved === "en") return saved;
  } catch {
    /* storage unavailable */
  }
  for (const l of navigator.languages ?? [navigator.language]) {
    const code = l.toLowerCase().slice(0, 2);
    if (code === "tk" || code === "ru" || code === "en") return code;
  }
  return "en";
}

// The current language also lives at module level so non-React code (error
// messages, date formatting) can use it; the provider keeps it in sync.
let current: Lang = detect();
document.documentElement.lang = current;

export const getLang = () => current;
export const getLocale = () => LOCALES[current];

function fill(s: string, vars?: Vars): string {
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : s;
}

export function translate(key: StrKey, vars?: Vars, lang: Lang = current): string {
  return fill(DICTS[lang][key] as string, vars);
}

export function translatePlural(key: PluralKey, n: number, vars?: Vars, lang: Lang = current): string {
  const forms = DICTS[lang][key] as Plural;
  const cat = new Intl.PluralRules(LOCALES[lang]).select(n) as keyof Plural;
  return fill(forms[cat] ?? forms.other, { n, ...vars });
}

export interface I18n {
  lang: Lang;
  locale: string;
  setLang: (l: Lang) => void;
  t: (key: StrKey, vars?: Vars) => string;
  tn: (key: PluralKey, n: number, vars?: Vars) => string;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ lang, setLang, children }: { lang: Lang; setLang: (l: Lang) => void; children: ReactNode }) {
  current = lang;
  const value = useMemo<I18n>(() => ({
    lang,
    locale: LOCALES[lang],
    setLang,
    t: (key, vars) => translate(key, vars, lang),
    tn: (key, n, vars) => translatePlural(key, n, vars, lang),
  }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

/** Persisting setter for the provider's owner. */
export function useLangSetter(set: (l: Lang) => void) {
  return useCallback((l: Lang) => {
    current = l;
    document.documentElement.lang = l;
    try {
      localStorage.setItem(KEY, l);
    } catch {
      /* storage unavailable */
    }
    set(l);
  }, [set]);
}

export { detect as initialLang };
