// Shared API mocks for the suites that render real screens, so a backend
// response-shape change is one edit.
import { vi } from 'vitest';

export const BV = 'GA Blue Value HIX Individual Network';

// `api` is the mocked `* as api from '<...>/api'` namespace of whichever test
// file calls this — vi.mock('./api')/vi.mock('../api') must already be in
// effect there (module mocking isn't shareable across files).
export function installDefaultMocks(api) {
  vi.resetAllMocks();
  window.history.replaceState(null, '', '/');
  // The screen waits for a plan until one is chosen. Default the suite to a
  // returning visitor with a saved plan, so each test exercises its own subject.
  try { localStorage.clear(); localStorage.setItem('hh_network_v1', BV); } catch { /* ignore */ }
  api.getNetworks.mockResolvedValue({ data: [{ network_name: BV, n_rates: 76197 }] });
  api.getHealth.mockResolvedValue({ data: {
    status: 'ok', priceable_npis: 27470, n_codes: 20697, as_of: '2026-08-28', networks: [BV],
  } });
  api.getPlans.mockResolvedValue({ data: [
    { plan: 'Blue Value HMO — Individual', carrier: 'Anthem', market: 'Individual (Georgia)',
      network_name: BV, available: true },
  ] });
  api.getPlanCostSharing.mockResolvedValue({ data: [] });
  api.getServiceLines.mockResolvedValue({ data: {} });
}
