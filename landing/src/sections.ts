// Section registry: page order for index.astro and the dev-only /preview/<id> route.
import Nav from './components/sections/Nav.astro';
import Hero from './components/sections/Hero.astro';
import Problema from './components/sections/Problema.astro';
import ComoFunciona from './components/sections/ComoFunciona.astro';
import Comercios from './components/sections/Comercios.astro';
import Prestamistas from './components/sections/Prestamistas.astro';
import Match from './components/sections/Match.astro';
import Confianza from './components/sections/Confianza.astro';
import Faq from './components/sections/Faq.astro';
import CtaFinal from './components/sections/CtaFinal.astro';
import Footer from './components/sections/Footer.astro';

export const mainSections = {
  hero: Hero,
  problema: Problema,
  'como-funciona': ComoFunciona,
  comercios: Comercios,
  prestamistas: Prestamistas,
  match: Match,
  confianza: Confianza,
  faq: Faq,
  'cta-final': CtaFinal,
};

export const chrome = { nav: Nav, footer: Footer };

// every section that /preview/<id> can render (the dev-only `screens` gallery is added by that page, so its
// CSS never reaches the production bundle)
export const allSections = { ...chrome, ...mainSections };
