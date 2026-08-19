import { createContext, useCallback, useContext, useMemo, useState } from "react";
import translations from "../i18n/translations";

const STORAGE_KEY = "yash.lang";
const VALID = ["en", "hi", "mr"];

function readLang() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (VALID.includes(v)) return v;
  } catch {}
  return "en";
}

const Ctx = createContext();

function interpolate(str, vars) {
  if (!vars) return str;
  return Object.entries(vars).reduce(
    (s, [k, v]) => s.replace(new RegExp(`\\{\\{${k}\\}\\}`, "g"), v),
    str
  );
}

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState(readLang);

  const set = useCallback((l) => {
    if (!VALID.includes(l)) return;
    setLang(l);
    try { localStorage.setItem(STORAGE_KEY, l); } catch {}
  }, []);

  const value = useMemo(() => ({ lang, setLang: set }), [lang, set]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang() {
  return useContext(Ctx);
}

/** Translation helper. Usage: t("dashboard.welcome", { name: "Ravi" }) */
export function useT() {
  const { lang } = useContext(Ctx);
  return useCallback(
    (key, vars) => {
      const dict = translations[lang] || translations.en;
      const val = dict[key] || translations.en[key] || key;
      return interpolate(val, vars);
    },
    [lang]
  );
}
