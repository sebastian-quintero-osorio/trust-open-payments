// Keeps hyphenated brand names (Bre-B) from breaking across lines: browsers may wrap after the hyphen.
const KEEP = ['Bre-B'];
const SPLIT = new RegExp(`(${KEEP.map((k) => k.replace(/[-]/g, '\\-')).join('|')})`);

export interface TextPart {
  text: string;
  nowrap: boolean;
}

export function nowrapParts(text: string): TextPart[] {
  return text
    .split(SPLIT)
    .filter(Boolean)
    .map((part) => ({ text: part, nowrap: KEEP.includes(part) }));
}
