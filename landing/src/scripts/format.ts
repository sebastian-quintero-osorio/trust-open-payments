// Number formatting shared by server-rendered markup and client count-ups (COPY.md conventions).
// Grouping is done by hand: Intl's es-CO output for 4-digit numbers varies by ICU build ("7200" vs "7.200").

/** no-break space, between a number and "%" (COPY.md conv.porcentaje: `15 %`) */
export const NBSP = ' ';

/** 48000 → "48.000" */
export const groupCO = (n: number): string => String(Math.round(Math.abs(n))).replace(/\B(?=(\d{3})+(?!\d))/g, '.');

/** 48000 → "$48.000" (COPY.md conv.dinero: no space after "$") */
export const formatCOP = (n: number, prefix = '$'): string => prefix + groupCO(n);

/** 15.3 → "15,3" */
export const formatDecimalCO = (n: number, digits = 1): string => n.toFixed(digits).replace('.', ',');

/** 15 → "15 %" with a no-break space */
export const pct = (n: number | string): string => `${n}${NBSP}%`;
