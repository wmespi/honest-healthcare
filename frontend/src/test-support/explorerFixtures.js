// Shared mock data + setup for the Explorer/App test suites (routes/Explorer.test.jsx
// and App.test.jsx) — one place for the API mocks both suites need, so a
// backend response-shape change is one edit, not two.
import { screen } from '@testing-library/react';
import { vi } from 'vitest';

export const BV = 'GA Blue Value HIX Individual Network';

export const OVERVIEW = {
  billing_code: 'ALL',
  billing_code_type: 'NETWORK',
  summary: { min: 5, max: 2000, avg: 200, median: 120, provider_groups: 30, n_providers: null, total_entries: 500 },
  distribution: [
    { rate: 0, type: 'fee schedule', provider_groups: 10 },
    { rate: 100, type: 'fee schedule', provider_groups: 20 },
  ],
};

export const CODE_DIST = {
  billing_code: '99213',
  billing_code_type: 'CPT',
  summary: { min: 40, max: 120, avg: 80, median: 82, provider_groups: 12, n_providers: 900, total_entries: 30 },
  distribution: [{ rate: 40, type: 'fee schedule', provider_groups: 6 }, { rate: 120, type: 'fee schedule', provider_groups: 6 }],
};

export const MENU = {
  npi: 123,
  count: 2,
  results: [
    { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'Evaluation & Management', min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3, n_networks: 1 },
    { billing_code: '45378', billing_code_type: 'CPT', label: 'Colonoscopy', rbcs_category: 'Procedure', min_rate: 162, median_rate: 214, max_rate: 352, n_rates: 4, n_networks: 1 },
  ],
};

// `api` is the mocked `* as api from '<...>/api'` namespace of whichever test
// file calls this — vi.mock('./api')/vi.mock('../api') must already be in
// effect there (module mocking isn't shareable across files).
export function installDefaultMocks(api) {
  vi.resetAllMocks();
  // Each test gets a clean URL — App reads deep-link params (?plan=&code=&npi=…)
  // once on mount, and writes them back via history.replaceState as state
  // changes, so a URL left over from a previous test would leak into the next.
  window.history.replaceState(null, '', '/');
  // The plan-first flow gates the whole page until a plan is chosen. Default the
  // suite to a "returning visitor" with a saved plan so each test exercises its
  // own subject; the gate itself has dedicated tests below.
  try { localStorage.clear(); localStorage.setItem('hh_network_v1', BV); } catch { /* ignore */ }
  api.getNetworks.mockResolvedValue({ data: [{ network_name: 'GA Blue Value HIX Individual Network', n_rates: 76197 }] });
  api.getHealth.mockResolvedValue({ data: {
    status: 'ok', priceable_npis: 27470, n_codes: 20697, as_of: '2026-08-28',
    networks: ['GA Blue Value HIX Individual Network', 'TRADITIONAL HEALTH PLAN', 'PARTICIPATING NETWORK HBP SPECIALTIES'],
  } });
  api.getPlans.mockResolvedValue({ data: [
    { plan: 'Blue Value HMO — Individual', carrier: 'Anthem', market: 'Individual (Georgia)',
      network_name: 'GA Blue Value HIX Individual Network', available: true },
  ] });
  api.getProcedureCategories.mockResolvedValue({ data: [] });
  api.searchBillingCodes.mockResolvedValue({ data: [] });
  api.getRateDistribution.mockResolvedValue({ data: OVERVIEW });
  api.getRatesByProvider.mockResolvedValue({ data: { billing_code: '99213', summary: { min: 40, max: 120, n_groups: 3, n_providers: 900 }, results: [] } });
  api.getRatesByNetwork.mockResolvedValue({ data: { billing_code: '99213', networks: [] } });
  api.getRateQuote.mockResolvedValue({ data: {
    billing_code: '99213', billing_code_type: 'CPT', npi: 123,
    headline: { rate: 82.05, max_rate: 82.05, basis: 'global', pos_label: 'Office / telehealth' },
    components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
      { pos_bucket: 'office', pos_label: 'Office / telehealth', min_rate: 82.05, max_rate: 82.05, negotiated_type: 'fee schedule' },
    ] }],
    is_component_split: false,
  } });
  api.getProviderMenu.mockResolvedValue({ data: MENU });
  api.searchProviders.mockResolvedValue({
    data: [{ npi: 123, name: 'ABBOTT, ASHLEY', city: 'ATLANTA', taxonomy_group: 'Family Medicine', has_rates: true, entity_type: 'individual' }],
  });
  api.getSpecialties.mockResolvedValue({
    data: [{ specialty: 'Cardiovascular Disease', n_providers: 300, n_with_rates: 236 }],
  });
  api.getServiceLines.mockResolvedValue({
    data: { pcp: { taxonomy_codes: ['207Q00000X'], billing_codes: ['99202', '99203', '99204', '99205', '99381', '99382', '99383', '99384', '99385', '99386', '99387'] } },
  });
}

export async function selectProvider(user) {
  const input = screen.getByPlaceholderText(/name or NPI/i);
  await user.click(input);
  await user.type(input, 'abbott');
  const opt = await screen.findByText('ABBOTT, ASHLEY');
  await user.click(opt);
}

// Pick the curated plan from the gate (or the network dropdown).
export async function selectPlan(user) {
  await user.click(screen.getByText('All Networks'));
  await user.click(await screen.findByText('Blue Value HMO — Individual'));
}
