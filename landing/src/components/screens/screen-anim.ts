// Visibility-driven controller for the app-screen mocks. Imported once, from DeviceFrame.astro's mock branch
// (a processed <script>, deduped per page), so the page ships a single entry and a single IntersectionObserver.
//  - toggles `.is-live` on every [data-screen] root while it intersects the viewport (CSS loops run only then)
//  - adds `.is-played` the first time ≥ 30 % of it is visible (one-shot CSS transitions) and starts the count-ups
//  - reduced motion: marks everything played at once and leaves the final values in place (no loops, see CSS)
import { countUp, prefersReducedMotion } from '../../scripts/motion';
import { formatCOP } from '../../scripts/format';

const PLAY_RATIO = 0.3;

function play(root: HTMLElement): void {
  root.classList.add('is-played');
  root.querySelectorAll<HTMLElement>('[data-count][data-live]').forEach((num) => {
    const to = Number(num.dataset.count);
    const prefix = num.dataset.prefix ?? '$';
    const target = num.querySelector<HTMLElement>('.sk-num__val');
    if (!target || !Number.isFinite(to)) return;
    // clamp: an rAF timestamp can precede countUp's start time, giving a slightly negative first frame
    const format = (n: number) => formatCOP(Math.min(to, Math.max(0, n)), prefix);
    target.textContent = format(0);
    const start = () => countUp(target, to, format, Number(num.dataset.ms) || 1500);
    const delay = Number(num.dataset.delay) || 0;
    if (delay) setTimeout(start, delay);
    else start();
  });
}

function init(): void {
  const roots = document.querySelectorAll<HTMLElement>('[data-screen]:not([data-screen-ready])');
  if (!roots.length) return;
  roots.forEach((root) => root.setAttribute('data-screen-ready', ''));

  if (prefersReducedMotion() || !('IntersectionObserver' in window)) {
    roots.forEach((root) => root.classList.add('is-played'));
    return;
  }

  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const root = entry.target as HTMLElement;
        root.classList.toggle('is-live', entry.isIntersecting);
        if (entry.isIntersecting && entry.intersectionRatio >= PLAY_RATIO && !root.classList.contains('is-played')) {
          play(root);
        }
      }
    },
    { threshold: [0, PLAY_RATIO] },
  );
  roots.forEach((root) => io.observe(root));
}

init();
