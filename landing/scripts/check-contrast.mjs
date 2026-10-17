#!/usr/bin/env node
// WCAG 2.x contrast audit for the token pairs the landing actually uses.
// Usage: node scripts/check-contrast.mjs   (exits 1 if any pair fails)
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const css = readFileSync(fileURLToPath(new URL('../src/styles/tokens.css', import.meta.url)), 'utf8');
const vars = Object.fromEntries(
  [...css.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(([, k, v]) => [k, v.trim()]),
);

function resolve(value, depth = 0) {
  const ref = value.match(/^var\(--([\w-]+)\)$/);
  if (ref && depth < 10) return resolve(vars[ref[1]], depth + 1);
  return value;
}

function parseColor(raw) {
  const v = resolve(raw);
  let m = v.match(/^#([0-9a-f]{6})$/i);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)).concat(1);
  m = v.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)\s*(?:[/,]\s*([\d.]+))?\s*\)$/);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  if (v === 'transparent') return [0, 0, 0, 0];
  throw new Error(`Cannot parse color "${raw}" -> "${v}"`);
}

const over = ([r, g, b, a], [R, G, B]) => [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a), 1];
const lum = ([r, g, b]) => {
  const c = [r, g, b].map((x) => {
    x /= 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (fg, bg) => {
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
};

// [foreground, background(s) stacked bottom→top, minimum ratio, usage]
const AA = 4.5;
const LARGE = 3; // >= 24px regular / 18.66px bold, and non-text UI (WCAG 1.4.11)
const pairs = [
  ['--text', ['--bg'], AA, 'body/headlines on page'],
  ['--text', ['--bg-deep'], AA, 'text on deepest wells'],
  ['--text', ['--bg', '--surface-2'], AA, 'text on glass cards'],
  ['--text-muted', ['--bg'], AA, 'secondary copy'],
  ['--text-muted', ['--bg', '--surface-2'], AA, 'secondary copy on cards'],
  ['--text-muted', ['--surface-solid'], AA, 'secondary copy on solid cards'],
  ['--text-subtle', ['--bg'], AA, 'captions, footnotes'],
  ['--text-subtle', ['--surface-solid'], AA, 'captions on solid cards'],
  ['--dark-text-muted', ['--surface-solid'], AA, 'app mock secondary text'],
  ['--dark-text-subtle', ['--surface-solid'], AA, 'app mock captions'],
  ['--text-accent', ['--bg'], AA, 'eyebrows, inline accents'],
  ['--text-accent', ['--surface-solid'], AA, 'accent on solid cards'],
  ['--text-accent', ['--violet-900'], AA, 'accent over deep violet glow'],
  ['--text', ['--violet-700'], AA, 'text over violet glow areas'],
  ['--text-accent-strong', ['--bg'], AA, 'large accent / gradient end stop'],
  ['--btn-primary-text', ['--btn-primary-bg'], AA, 'primary button'],
  ['--btn-primary-text', ['--btn-primary-bg-hover'], AA, 'primary button hover'],
  ['--btn-violet-text', ['--btn-violet-bg'], AA, 'violet button'],
  ['--btn-violet-text', ['--btn-violet-bg-hover'], AA, 'violet button hover'],
  ['--trust-lilac', ['--trust-violet'], AA, 'lilac text on violet chip'],
  ['--focus-ring', ['--bg'], LARGE, 'focus ring (non-text)'],
  ['--focus-ring-light', ['--light-bg'], LARGE, 'focus ring on light sections'],
  ['--border-input', ['--bg'], LARGE, 'form field border (non-text)'],
  ['--light-text', ['--light-bg'], AA, 'text on light sections'],
  ['--light-muted', ['--light-bg'], AA, 'secondary on light sections'],
  ['--light-muted', ['--light-surface'], AA, 'secondary on light cards'],
  ['--light-accent', ['--light-bg'], AA, 'violet text on light sections'],
  ['--light-accent-strong', ['--light-surface'], AA, 'violet text on light cards'],
];

let failed = 0;
for (const [fgName, bgStack, min, usage] of pairs) {
  const base = parseColor(`var(${bgStack[0]})`);
  const bg = bgStack.slice(1).reduce((acc, n) => over(parseColor(`var(${n})`), acc), base);
  const fg = over(parseColor(`var(${fgName})`), bg);
  const r = ratio(fg, bg);
  const ok = r >= min;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${r.toFixed(2).padStart(5)}:1  (min ${min})  ${fgName} on ${bgStack.join(' + ')}  — ${usage}`);
}
console.log(failed ? `\n${failed} pair(s) below WCAG AA` : '\nAll pairs meet WCAG AA');
process.exit(failed ? 1 : 0);
