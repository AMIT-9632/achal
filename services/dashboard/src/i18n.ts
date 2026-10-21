// SPDX-License-Identifier: MIT
import en from "./i18n/en.json";
import hi from "./i18n/hi.json";
import bn from "./i18n/bn.json";
import sat from "./i18n/sat.json";

export const dictionaries = { en, hi, bn, sat } as const;
export type Lang = keyof typeof dictionaries;
export const languageNames: Record<Lang, string> = { en: "English", hi: "हिन्दी", bn: "বাংলা", sat: "ᱥᱟᱱᱛᱟᱲᱤ" };

// Santali readers in Jharkhand usually read Hindi too, so missing Santali
// strings fall back to Hindi before English.
const fallbacks: Record<Lang, Lang[]> = { en: [], hi: ["en"], bn: ["en"], sat: ["hi", "en"] };

export type T = (key: string) => string;

export function translator(lang: Lang): T {
  const chain = [lang, ...fallbacks[lang]].map((l) => dictionaries[l] as Record<string, string>);
  return (key: string) => {
    for (const d of chain) if (d[key] !== undefined) return d[key];
    return key;
  };
}

/** True while a language's strings are an unreviewed draft (shown as a banner). */
export const isDraft = (lang: Lang) => (dictionaries[lang] as Record<string, string>)._status === "draft-unreviewed";

export function initialLang(): Lang {
  const fromUrl = new URLSearchParams(location.search).get("lang");
  let saved: string | null = null;
  try {
    saved = localStorage.getItem("achal.lang");
  } catch {
    /* storage blocked */
  }
  for (const c of [fromUrl, saved]) if (c && c in dictionaries) return c as Lang;
  return "hi";
}
