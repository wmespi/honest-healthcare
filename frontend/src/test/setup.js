import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// jsdom doesn't implement these; recharts / framer-motion / the app touch them.
window.scrollTo = vi.fn();

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
  });
}
