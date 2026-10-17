/**
 * Hero aurora: a tiny WebGL fragment shader that adds slow light ribbons rising from the CSS horizon.
 * Progressive enhancement only — the CSS horizon underneath is the complete design on its own.
 * Statically imported by Horizon.astro (a dynamic import would cost more than the shader) and initialised
 * after load + idle; never runs with reduced motion, Save-Data or without WebGL. It draws at 30 fps through
 *
 * Geometry comes from the DOM: `probe` is an invisible box whose width is the planet diameter and whose
 * top edge is the rim apex, so the ribbons follow the exact arc drawn by the CSS gradient.
 */
import { visibleLoop } from '../../scripts/motion';

// Uniforms: r = canvas size, c = planet center, R = planet radius (all in canvas px), t = seconds.
const VS = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
const FS = `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 r,c;uniform float R,t;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+1.),f.x),f.y);}
float b(vec2 p){float v=0.,a=.5;for(int i=0;i<4;i++){v+=a*n(p);p=p*2.03+17.;a*=.5;}return v;}
void main(){
vec2 q=gl_FragCoord.xy-c;
float d=(length(q)-R)/r.y,s=atan(q.x,q.y)*R/r.y;
float w=b(vec2(s*1.3+t*.018,t*.035));
float k=b(vec2(s*2.6+w*2.2-t*.025,d*1.4-t*.05));
float f=smoothstep(-.012,.04,d)*exp(-d*4.)*exp(-s*s*2.4);
float a=f*smoothstep(.4,.9,k)*(.8+.2*sin(s*170.+w*18.));
float m=exp(-abs(d)*110.)*smoothstep(.35,.85,b(vec2(s*3.5-t*.1,t*.04)))*exp(-s*s*.9);
vec3 col=mix(vec3(.41,.19,.98),vec3(.62,.42,.99),smoothstep(.45,1.,k))*a*.55+vec3(.95,.92,1.)*m*.4;
gl_FragColor=vec4(col,max(a*.55,m*.4));
}`;

export function initAurora(cv: HTMLCanvasElement, probe: HTMLElement): void {
  const gl = cv.getContext('webgl', {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: 'low-power',
  });
  if (!gl) return;

  let teardown: (() => void) | undefined;

  /** compiles + links without blocking the main thread where the driver allows it, then starts drawing */
  const setup = () => {
    let cancelled = false;
    let stop: (() => void) | undefined;
    let ro: ResizeObserver | undefined;
    let poll = 0;
    teardown = () => {
      cancelled = true;
      cancelAnimationFrame(poll);
      stop?.();
      ro?.disconnect();
      cv.classList.remove('is-on');
    };

    const parallel = gl.getExtension('KHR_parallel_shader_compile');
    const pr = gl.createProgram();
    if (!pr) return;
    for (const [type, src] of [
      [gl.VERTEX_SHADER, VS],
      [gl.FRAGMENT_SHADER, FS],
    ] as const) {
      const s = gl.createShader(type);
      if (!s) return;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      gl.attachShader(pr, s);
    }
    gl.bindAttribLocation(pr, 0, 'p');
    gl.linkProgram(pr);

    const start = () => {
      if (cancelled || gl.isContextLost()) return;
      // reading LINK_STATUS blocks until the link is done: only do it once the driver reports completion
      if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) {
        if (import.meta.env.DEV) console.warn('[aurora]', gl.getProgramInfoLog(pr));
        return;
      }
      gl.useProgram(pr);
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      // one oversized triangle covers the viewport
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

      const u = (name: string) => gl.getUniformLocation(pr, name);
      const uRes = u('r'),
        uC = u('c'),
        uRad = u('R'),
        uT = u('t');

      const fit = () => {
        const b = cv.getBoundingClientRect();
        const p = probe.getBoundingClientRect();
        if (!b.width || !b.height) return;
        // render at <= 0.5 CSS px per canvas px, internal width capped at 900; CSS upscales it
        const k = Math.min(0.5, 900 / b.width);
        cv.width = Math.round(b.width * k);
        cv.height = Math.round(b.height * k);
        gl.viewport(0, 0, cv.width, cv.height);
        const rad = p.width / 2;
        gl.uniform2f(uRes, cv.width, cv.height);
        gl.uniform2f(uC, (p.left + rad - b.left) * k, (b.bottom - p.top - rad) * k);
        gl.uniform1f(uRad, rad * k);
      };
      fit();
      ro = new ResizeObserver(fit);
      ro.observe(cv);

      let shown = false;
      // 30 fps is plenty for slow light, and capping the loop itself (not skipping frames inside a 60 Hz
      // rAF) keeps the page from producing a main-thread frame every 16 ms
      stop = visibleLoop(
        cv,
        (sec) => {
          gl.uniform1f(uT, sec + 40);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          if (!shown) {
            shown = true;
            cv.classList.add('is-on');
          }
        },
        { fps: 30 },
      );
    };

    if (!parallel) return start();
    const waitForLink = () => {
      if (cancelled) return;
      if (gl.getProgramParameter(pr, parallel.COMPLETION_STATUS_KHR)) start();
      else poll = requestAnimationFrame(waitForLink);
    };
    waitForLink();
  };

  cv.addEventListener('webglcontextlost', (e) => {
    e.preventDefault(); // tells the browser we will rebuild on `webglcontextrestored`
    teardown?.();
  });
  cv.addEventListener('webglcontextrestored', setup);
  setup();
}
