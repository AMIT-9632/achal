// SPDX-License-Identifier: MIT
import en from "./i18n/en.json";
import hi from "./i18n/hi.json";
import bn from "./i18n/bn.json";
import sat from "./i18n/sat.json";

export const dictionaries = { en, hi, bn, sat } as const;
export type Lang = keyof typeof dictionaries;
export const languageNames: Record<Lang, string> = { en: "English", hi: "हिन्दी", bn: "বাংলা", sat: "ᱥᱟᱱᱛᱟᱲᱤ" };

export function translator(lang: Lang) {
  const dict = dictionaries[lang] as Record<string, string>;
  return (key: string) => dict[key] ?? (dictionaries.en as Record<string, string>)[key] ?? key;
}
