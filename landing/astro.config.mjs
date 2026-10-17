// @ts-check
import { defineConfig, fontProviders } from 'astro/config';
import { loadEnv } from 'vite';

// The config runs before Vite loads .env files, so read them here (process.env still wins, as in Vite).
const isBuild = process.argv.includes('build');
const mode = process.env.NODE_ENV ?? (isBuild ? 'production' : 'development');
const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env };
const FALLBACK_SITE = 'https://trust.example.com';

if (isBuild) {
  /** @param {string} msg */
  const warn = (msg) => console.warn(`\x1b[33m[trust] ${msg}\x1b[0m`);
  if (!env.PUBLIC_SITE_URL)
    warn(`PUBLIC_SITE_URL is not set: canonical, og:url, og:image and JSON-LD will point to ${FALLBACK_SITE}.`);
  if (!env.PUBLIC_WAITLIST_ENDPOINT)
    warn('PUBLIC_WAITLIST_ENDPOINT is not set: the waitlist form will say it is not connected and send nothing.');
}

// https://astro.build/config
export default defineConfig({
  // Canonical/OG URLs. Set PUBLIC_SITE_URL (env or .env) when the production domain is known.
  site: env.PUBLIC_SITE_URL || FALLBACK_SITE,
  // Astro 7 defaults to 'jsx', which drops the space between inline elements on separate lines.
  compressHTML: true,
  build: {
    // One-page site: inline all CSS so first paint never waits on a stylesheet request.
    inlineStylesheets: 'always',
  },
  vite: {
    build: {
      // Explicit CSS targets are required: when the list has no Safari/Firefox, the Lightning CSS
      // minifier folds `animation-timeline` into the `animation` shorthand, which Chrome rejects,
      // silently killing every scroll-driven animation in production builds (dev is unaffected).
      cssTarget: ['chrome111', 'edge111', 'firefox128', 'safari16.4', 'ios16.4'],
    },
  },
  fonts: [
    {
      // Qontra Regular (Tomaz Hrastar) + Spanish glyphs added by scripts/fonts/patch-qontra.py
      provider: fontProviders.local(),
      name: 'Qontra Trust',
      cssVariable: '--font-qontra',
      fallbacks: ['sans-serif'],
      options: {
        variants: [{ src: ['./src/assets/fonts/qontra-trust.woff2'], weight: 400, style: 'normal' }],
      },
    },
    {
      // Barlow (OFL): DIN roots like Qontra and the squared geometry of the wordmark (docs/DESIGN.md §4)
      provider: fontProviders.local(),
      name: 'Barlow',
      cssVariable: '--font-barlow',
      fallbacks: ['ui-sans-serif', 'system-ui', 'sans-serif'],
      options: {
        variants: [
          { src: ['./src/assets/fonts/barlow-latin-400.woff2'], weight: 400, style: 'normal' },
          { src: ['./src/assets/fonts/barlow-latin-600.woff2'], weight: 600, style: 'normal' },
        ],
      },
    },
  ],
});
