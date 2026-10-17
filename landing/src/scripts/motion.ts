// Shared helpers for client-side motion. Import from component <script> tags (bundled by Astro).

export const prefersReducedMotion = (): boolean =>
  matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Calls `onChange(true|false)` as the element enters/leaves the viewport. Returns a disposer. */
export function whenVisible(
  el: Element,
  onChange: (visible: boolean) => void,
  options: IntersectionObserverInit = {},
): () => void {
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) onChange(entry.isIntersecting);
    },
    { rootMargin: '120px 0px', ...options },
  );
  io.observe(el);
  return () => io.disconnect();
}

/** Runs `cb` once, the first time the element becomes visible. */
export function onceVisible(el: Element, cb: () => void, threshold = 0.25): void {
  const io = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      cb();
    },
    { threshold },
  );
  io.observe(el);
}

export interface LoopOptions {
  /** cap the frame rate: frames are scheduled with a timer + rAF, so the page is not woken at 60 Hz */
  fps?: number;
}

/**
 * Frame loop that only runs while the element is on screen and the tab is visible. `tick` receives elapsed
 * seconds of *running* time (it does not jump after the loop was stopped offscreen). Returns a disposer.
 */
export function visibleLoop(el: Element, tick: (seconds: number) => void, options: LoopOptions = {}): () => void {
  const interval = options.fps ? 1000 / options.fps : 0;
  let raf = 0;
  let timer = 0;
  let running = false;
  let onScreen = false;
  let elapsed = 0; // ms accumulated while running
  let last = -1; // timestamp of the previous frame in this run

  const frame = (now: number) => {
    raf = 0;
    if (!running) return;
    if (last >= 0) elapsed += Math.min(now - last, 250); // a long gap (jank, throttling) is not motion
    last = now;
    tick(elapsed / 1000);
    if (!interval) {
      raf = requestAnimationFrame(frame);
      return;
    }
    // wake up just before the frame that is `interval` ms after this one (half a 60 Hz frame of slack)
    const wait = interval - (performance.now() - now) - 8;
    timer = window.setTimeout(() => {
      timer = 0;
      if (running) raf = requestAnimationFrame(frame);
    }, Math.max(0, wait));
  };

  const sync = () => {
    const shouldRun = onScreen && document.visibilityState === 'visible';
    if (shouldRun === running) return;
    running = shouldRun;
    if (running) {
      last = -1;
      raf = requestAnimationFrame(frame);
    } else {
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
      raf = timer = 0;
    }
  };

  const stopObserving = whenVisible(el, (v) => {
    onScreen = v;
    sync();
  });
  document.addEventListener('visibilitychange', sync);
  return () => {
    stopObserving();
    document.removeEventListener('visibilitychange', sync);
    running = false;
    if (raf) cancelAnimationFrame(raf);
    if (timer) clearTimeout(timer);
    raf = timer = 0;
  };
}

/** Animates a number inside `el` from 0 to `to` with the brand ease-out (respects reduced motion). */
export function countUp(el: HTMLElement, to: number, format: (n: number) => string, ms = 1400): void {
  if (prefersReducedMotion()) {
    el.textContent = format(to);
    return;
  }
  const t0 = performance.now();
  const ease = (t: number) => 1 - Math.pow(1 - t, 4);
  const step = (now: number) => {
    // rAF timestamps can precede t0 on the first frame; clamp so the value never goes negative
    const t = Math.max(0, Math.min(1, (now - t0) / ms));
    el.textContent = format(to * ease(t));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
