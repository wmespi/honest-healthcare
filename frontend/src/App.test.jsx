import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';
import App from './App';

vi.mock('./api');

const BV = 'GA Blue Value HIX Individual Network';

const OVERVIEW = {
  billing_code: 'ALL',
  billing_code_type: 'NETWORK',
  summary: { min: 5, max: 2000, avg: 200, median: 120, provider_groups: 30, n_providers: null, total_entries: 500 },
  distribution: [
    { rate: 0, type: 'fee schedule', provider_groups: 10 },
    { rate: 100, type: 'fee schedule', provider_groups: 20 },
  ],
};

const CODE_DIST = {
  billing_code: '99213',
  billing_code_type: 'CPT',
  summary: { min: 40, max: 120, avg: 80, median: 82, provider_groups: 12, n_providers: 900, total_entries: 30 },
  distribution: [{ rate: 40, type: 'fee schedule', provider_groups: 6 }, { rate: 120, type: 'fee schedule', provider_groups: 6 }],
};

const MENU = {
  npi: 123,
  count: 2,
  results: [
    { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'Evaluation & Management', min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3, n_networks: 1 },
    { billing_code: '45378', billing_code_type: 'CPT', label: 'Colonoscopy', rbcs_category: 'Procedure', min_rate: 162, median_rate: 214, max_rate: 352, n_rates: 4, n_networks: 1 },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
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
});

async function selectProvider(user) {
  const input = screen.getByPlaceholderText(/name or NPI/i);
  await user.click(input);
  await user.type(input, 'abbott');
  const opt = await screen.findByText('ABBOTT, ASHLEY');
  await user.click(opt);
}

// Pick the curated plan from the gate (or the network dropdown).
async function selectPlan(user) {
  await user.click(screen.getByText('All Networks'));
  await user.click(await screen.findByText('Blue Value HMO — Individual'));
}

// Regression: a provider selected with no procedure must NOT trigger an
// npi-only /rates/distribution call (it full-scans and hangs — see the
// "QUERYING MRF DATA..." spinner that never resolves). It should show the menu.
describe('provider selected without a procedure', () => {
  it('renders the procedure menu and never requests an npi-only distribution', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await selectProvider(user);

    // The menu view loads.
    expect(await screen.findByText(/procedure menu/i)).toBeInTheDocument();
    expect(api.getProviderMenu).toHaveBeenCalledWith('123', BV, undefined, '', 'plausible');

    // ...and the app is not stuck on the loading spinner.
    expect(screen.queryByText(/querying mrf data/i)).not.toBeInTheDocument();

    // Every distribution call that carried an npi also carried a billing_code.
    for (const [code, , , , npiArg] of api.getRateDistribution.mock.calls) {
      if (npiArg) expect(code).toBeTruthy();
    }
  });

  it('drills into a procedure when a menu row is clicked', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await selectProvider(user);
    await screen.findByText(/procedure menu/i);

    // Expand the category, then click the procedure.
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    await waitFor(() =>
      expect(api.getRateDistribution).toHaveBeenCalledWith(
        '99213', 'CPT', BV, undefined, '123', undefined,
      ),
    );
    // With a provider active, the cost card (job 1) is fetched — not the
    // compare-across-providers table.
    await waitFor(() => expect(api.getRateQuote).toHaveBeenCalledWith('99213', 'CPT', '123', BV));
    expect(api.getRatesByProvider).not.toHaveBeenCalled();
    expect(await screen.findByText(/negotiated cost/i)).toBeInTheDocument();
  });
});

describe('network comparison (job 2)', () => {
  it('shows "Does your plan matter?" with a per-network breakdown', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.searchBillingCodes.mockResolvedValue({ data: [
      { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', provider_groups: 12 },
    ] });
    api.getRatesByNetwork.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT',
      networks: [
        { network_name: 'GA Blue Value HIX Individual Network', median: 253, min: 162, max: 353, typical_low: 253, typical_high: 258, spread: 1.0, n_groups: 31, n_providers: 6566 },
        { network_name: 'TRADITIONAL HEALTH PLAN', median: 363, min: 11, max: 10379, typical_low: 225, typical_high: 535, spread: 2.4, n_groups: 6286, n_providers: 71210 },
      ],
    } });

    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/does your plan matter/i)).toBeInTheDocument();
    expect(screen.getAllByText('Blue Value (HMO)').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Traditional (PPO)').length).toBeGreaterThan(0);
    expect(screen.getByText(/flat rate/i)).toBeInTheDocument();
    expect(screen.getByText(/2\.4× provider spread/i)).toBeInTheDocument();
    // issue #11 — provider count shown alongside group count
    expect(screen.getByText(/6,566 providers · 31 groups/)).toBeInTheDocument();
  });
});

describe('cross-specialty rollup caveat', () => {
  it('warns when the procedure is unlikely for the provider\'s specialty', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'ABBOTT, ASHLEY', specialty: 'Social Worker', city: 'RIVERDALE' },
      plausibility: 'unlikely',
      headline: { rate: 90, max_rate: 14029, basis: 'global', pos_label: null },
      components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
        { pos_bucket: 'any', pos_label: 'Any setting', min_rate: 90, max_rate: 14029, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/group-contracted rate/i)).toBeInTheDocument();
    expect(screen.getByText(/no record of whether/i)).toBeInTheDocument();
    // numbers are tucked behind a disclosure, not shown as the headline
    expect(screen.getByText(/show the group rate/i)).toBeInTheDocument();
  });
});

describe('Medicare utilization evidence (issue #14)', () => {
  it('shows a "billed to Medicare" line on the cost card when the provider bills the code', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'ABBOTT, ASHLEY', specialty: 'Family Medicine', city: 'ATLANTA' },
      plausibility: 'typical',
      medicare_utilization: { billed: true, year: 2024, tot_srvcs: 142, tot_benes: 90, avg_mdcr_allowed: 83.4, is_drug: false },
      headline: { rate: 82, max_rate: 82, basis: 'global', pos_label: 'Office / telehealth' },
      components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
        { pos_bucket: 'office', pos_label: 'Office / telehealth', min_rate: 82, max_rate: 82, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/billed this to Medicare/i)).toBeInTheDocument();
    expect(screen.getByText(/142 times in 2024/i)).toBeInTheDocument();
  });

  it('frames the cost card as a group rate when the CMS tier is "group"', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '59514', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'BACON COUNTY HEALTH SERVICES', specialty: 'General Acute Care Hospital', city: 'ALMA' },
      plausibility: null,
      tier: 'group',
      medicare_utilization: { billed: false, year: 2024 },
      headline: { rate: 207.68, max_rate: 3150, basis: 'global', pos_label: null },
      components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
        { pos_bucket: 'any', pos_label: 'Any setting', min_rate: 207.68, max_rate: 3150, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/group-contracted rate/i)).toBeInTheDocument();
    expect(screen.getByText(/show the group rate/i)).toBeInTheDocument();
  });

  it('collapses group-tier rates behind "show all" and expands on click', async () => {
    const user = userEvent.setup();
    api.getProviderMenu.mockImplementation((npi, net, setting, q = '', tier = 'plausible') => {
      if (tier === 'all') return Promise.resolve({ data: {
        npi: 123, tier: 'all', group_count: 1, specialty: 'Cardiology', count: 2,
        results: [
          { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'Evaluation & Management', min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3, n_networks: 1, tier: 'typical' },
          { billing_code: '11111', billing_code_type: 'CPT', label: 'Random Surgery', rbcs_category: 'Procedure', min_rate: 900, median_rate: 900, max_rate: 900, n_rates: 1, n_networks: 1, tier: 'group' },
        ],
      } });
      return Promise.resolve({ data: {
        npi: 123, tier: 'plausible', group_count: 1, specialty: 'Cardiology', count: 1,
        results: [
          { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'Evaluation & Management', min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3, n_networks: 1, tier: 'typical' },
        ],
      } });
    });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);

    const showAll = await screen.findByText(/1 more rates contracted/i);
    await user.click(showAll);

    await waitFor(() => expect(api.getProviderMenu).toHaveBeenCalledWith('123', BV, undefined, '', 'all'));
    await user.click(await screen.findByText('Procedure'));
    expect(await screen.findByText('Random Surgery')).toBeInTheDocument();
  });

  it('badges menu rows the provider billed to Medicare', async () => {
    const user = userEvent.setup();
    api.getProviderMenu.mockResolvedValue({ data: { npi: 123, count: 1, results: [
      { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'Evaluation & Management',
        min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3, n_networks: 1,
        medicare: { tot_srvcs: 210, tot_benes: 130, year: 2024 } },
    ] } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));

    expect(await screen.findByText('Medicare')).toBeInTheDocument();
  });

  it('marks provider search results with no rate in the picked plan — and makes them unpickable', async () => {
    const user = userEvent.setup();
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 111, name: 'ALPHARETTA CARDIOLOGY, LLC', city: 'ALPHARETTA', specialty: 'Cardiovascular Disease', has_rates: true },
      { npi: 222, name: 'CARDIOLOGY CARE CLINIC, LLC', city: 'EATONTON', specialty: 'Cardiac Facilities', has_rates: false },
    ] });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    const input = screen.getByPlaceholderText(/name or NPI/i);
    await user.click(input);
    await user.type(input, 'cardio');

    // network is scoped to the saved plan → no-rate rows say "not in <plan>"
    expect(await screen.findByText('has rates')).toBeInTheDocument();
    expect(screen.getAllByText(/not in Blue Value/i).length).toBeGreaterThanOrEqual(2); // header + row
    expect(screen.getByText(/Not in Blue Value.*1 more/i)).toBeInTheDocument();

    // the out-of-plan provider is a listing, not a button — clicking it is inert
    const deadRow = screen.getByText('CARDIOLOGY CARE CLINIC, LLC');
    expect(deadRow.closest('button')).toBeNull();
    await user.click(deadRow);
    expect(screen.queryByText(/No negotiated rates for/i)).not.toBeInTheDocument();
  });

  it('still labels rows plainly "no rate data" when browsing without a plan', async () => {
    const user = userEvent.setup();
    try { localStorage.removeItem('hh_network_v1'); } catch { /* ignore */ }
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 111, name: 'ALPHARETTA CARDIOLOGY, LLC', city: 'ALPHARETTA', specialty: 'Cardiovascular Disease', has_rates: true },
      { npi: 222, name: 'CARDIOLOGY CARE CLINIC, LLC', city: 'EATONTON', specialty: 'Cardiac Facilities', has_rates: false },
    ] });
    render(<App />);
    await user.click(await screen.findByText(/explore all networks without picking a plan/i));
    const input = screen.getByPlaceholderText(/name or NPI/i);
    await user.click(input);
    await user.type(input, 'cardio');

    expect(await screen.findByText('no rate data')).toBeInTheDocument();
    expect(screen.getByText(/No rate data — 1 more/i)).toBeInTheDocument();
    // no plan → still pickable
    expect(screen.getByText('CARDIOLOGY CARE CLINIC, LLC').closest('button')).not.toBeNull();
  });
});

describe('out-of-pocket estimator (issue #30)', () => {
  beforeEach(() => {
    try { localStorage.clear(); localStorage.setItem('hh_network_v1', BV); } catch { /* ignore */ }
  });

  it('shows "You\'d pay ≈" on the cost card once cost-sharing is entered', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'ABBOTT, ASHLEY', specialty: 'Family Medicine', city: 'ATLANTA' },
      plausibility: 'typical', tier: 'typical',
      headline: { rate: 200, max_rate: 200, basis: 'global', pos_label: 'Office / telehealth' },
      components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
        { pos_bucket: 'office', pos_label: 'Office / telehealth', min_rate: 200, max_rate: 200, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await user.click(screen.getByText('Your cost sharing'));
    const coins = await screen.findByPlaceholderText('20');
    await user.type(coins, '20');

    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    // deductible unset -> 20% of $200
    expect(await screen.findByText(/You'd pay ≈/)).toBeInTheDocument();
    expect(screen.getAllByText(/\$40\.00/).length).toBeGreaterThan(0);
  });

  it('persists plan params to localStorage', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await user.click(screen.getByText('Your cost sharing'));
    await user.type(await screen.findByPlaceholderText('20'), '15');
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem('hh_plan_v1'));
      expect(saved.coinsurance).toBe('15');
    });
  });
});

describe('friendly plan picker (issue #33)', () => {
  beforeEach(() => { try { localStorage.removeItem('hh_network_v1'); } catch { /* ignore */ } });

  it('offers the curated plan and resolves it to its network', async () => {
    const user = userEvent.setup();
    api.getNetworks.mockResolvedValue({ data: [
      { network_name: 'GA Blue Value HIX Individual Network', n_rates: 76197 },
      { network_name: 'TRADITIONAL HEALTH PLAN', n_rates: 7000000 },
    ] });
    render(<App />);
    await waitFor(() => expect(api.getPlans).toHaveBeenCalled());

    await user.click(screen.getByText('All Networks'));
    expect(await screen.findByText('Your plan')).toBeInTheDocument();
    await user.click(screen.getByText('Blue Value HMO — Individual'));

    // the network filter is applied → distribution re-fetched for that network
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalledWith(
      undefined, undefined, 'GA Blue Value HIX Individual Network', undefined, undefined, undefined,
    ));
    // dropdown closes; the button now shows the friendly label
    await waitFor(() => expect(screen.queryByText('Your plan')).not.toBeInTheDocument());
    expect(screen.getByText('Blue Value HMO — Individual')).toBeInTheDocument();
  });
});

describe('trust bar (issue #32)', () => {
  beforeEach(() => { try { localStorage.clear(); } catch { /* ignore */ } });

  it('shows dataset coverage + the "all networks mixes data" warning, and dismisses', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());

    expect(await screen.findByText(/27,470/)).toBeInTheDocument();
    expect(screen.getByText(/rates as of/i)).toBeInTheDocument();
    expect(screen.getByText(/mixes GA Blue Value with national mirror data/i)).toBeInTheDocument();

    await user.click(screen.getByLabelText('Dismiss'));
    await waitFor(() => expect(screen.queryByText(/27,470/)).not.toBeInTheDocument());
    expect(localStorage.getItem('hh_trustbar_dismissed')).toBe('1');
  });
});

describe('specialty scope filter (issue #31 rework)', () => {
  it('is a separate filter from picking a provider, and scopes the results', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.searchBillingCodes.mockResolvedValue({ data: [
      { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'E&M', provider_groups: 12 },
    ] });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    // pick a procedure
    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    // now scope to a specialty — its own dropdown, default "All specialties"
    await user.click(screen.getByText('All specialties'));
    await user.click(await screen.findByText('Cardiovascular Disease'));

    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalledWith(
      '99213', 'CPT', BV, undefined, undefined, 'Cardiovascular Disease',
    ));
    await waitFor(() => expect(api.getRatesByProvider).toHaveBeenCalledWith(
      '99213', 'CPT', BV, undefined, undefined,
      expect.objectContaining({ specialty: 'Cardiovascular Disease' }),
    ));
    // the provider (name) search is untouched — still its own control
    expect(screen.getByPlaceholderText(/name or NPI/i)).toBeInTheDocument();
  });
});

describe('provider with no rates in the selected network', () => {
  it('shows an explicit empty state instead of a blank screen', async () => {
    const user = userEvent.setup();
    api.getProviderMenu.mockResolvedValue({ data: { npi: 123, count: 0, results: [] } });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await selectProvider(user);

    expect(await screen.findByText(/no negotiated rates for/i)).toBeInTheDocument();
    expect(screen.queryByText(/querying mrf data/i)).not.toBeInTheDocument();
  });
});

describe('procedure search scoping', () => {
  it('queries the provider menu (not the global catalog) once a provider is selected', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);

    api.searchBillingCodes.mockClear();
    api.getProviderMenu.mockClear();

    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'destruction');

    await waitFor(() => expect(api.getProviderMenu).toHaveBeenCalledWith('123', BV, undefined, 'destruction'));
    expect(api.searchBillingCodes).not.toHaveBeenCalled();
  });
});

describe('compare-across-providers view', () => {
  it('shows "Does the provider matter?" with named practices when a code is picked and no provider', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.searchBillingCodes.mockResolvedValue({ data: [
      { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', provider_groups: 12 },
    ] });
    api.getRatesByProvider.mockResolvedValue({ data: {
      billing_code: '99213', component: 'global',
      summary: { min: 56.84, max: 123.08, median: 90.5, n_practices: 989, n_groups: 12, n_providers: 6566 },
      results: [
        { practice_id: '1', practice_name: 'MOON DERMATOLOGY', min_rate: 123.08, max_rate: 123.08, median_rate: 123.08, npi_count: 1, n_groups: 1, ga_taxonomies: [], ga_hospital_npis: 0 },
        { practice_id: '2', practice_name: 'EMORY MEDICAL CARE FOUNDATION INC', min_rate: 56.84, max_rate: 123.08, median_rate: 90.5, npi_count: 5643, n_groups: 6, ga_taxonomies: [], ga_hospital_npis: 1 },
      ],
    } });

    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    // plan is already chosen (plan-first flow) — the compare view fills in
    expect(await screen.findByText(/does the provider matter/i)).toBeInTheDocument();
    expect(screen.getByText(/Moon Dermatology/i)).toBeInTheDocument();
    expect(screen.getByText(/on the standard/i)).toBeInTheDocument();
    expect(api.getRatesByProvider).toHaveBeenCalledWith(
      '99213', 'CPT', 'GA Blue Value HIX Individual Network', undefined, undefined,
      expect.objectContaining({ specialty: undefined }));
  });
});

describe('default landing state', () => {
  it('loads the overview for the saved plan, no code or npi', async () => {
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    expect(api.getRateDistribution).toHaveBeenCalledWith(undefined, undefined, BV, undefined, undefined, undefined);
    expect(api.getProviderMenu).not.toHaveBeenCalled();
  });
});

describe('plan-first gate', () => {
  beforeEach(() => { try { localStorage.removeItem('hh_network_v1'); } catch { /* ignore */ } });

  it('blocks the explorer until a plan is chosen, then loads it', async () => {
    const user = userEvent.setup();
    render(<App />);

    // gated: the "start with your plan" step, no data call, no search box
    expect(await screen.findByText(/start with your plan/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search procedure or billing code/i)).not.toBeInTheDocument();
    expect(api.getRateDistribution).not.toHaveBeenCalled();

    await selectPlan(user);

    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalledWith(
      undefined, undefined, BV, undefined, undefined, undefined));
    expect(screen.queryByText(/start with your plan/i)).not.toBeInTheDocument();
    // and the plan is remembered
    expect(localStorage.getItem('hh_network_v1')).toBe(BV);
  });

  it('lets the user bypass the gate to browse all networks', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByText(/explore all networks without picking a plan/i));

    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalledWith(
      undefined, undefined, undefined, undefined, undefined, undefined));
    expect(screen.getByPlaceholderText(/search procedure or billing code/i)).toBeInTheDocument();
  });
});

describe('specialty-first flow', () => {
  it('plan → specialty → a ranked provider list', async () => {
    const user = userEvent.setup();
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 123, name: 'ABBOTT, ASHLEY', city: 'ATLANTA', specialty: 'Cardiovascular Disease', has_rates: true, entity_type: 'individual' },
      { npi: 456, name: 'NO RATES CLINIC', city: 'MACON', specialty: 'Cardiovascular Disease', has_rates: false, entity_type: 'organization' },
    ] });
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    // the care step: alphabetical specialty list with provider counts shown
    expect(await screen.findByText(/what kind of care do you need/i)).toBeInTheDocument();
    await user.click(await screen.findByText('Cardiovascular Disease'));

    // provider list for that specialty, scoped to the plan
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', 'Cardiovascular Disease', 40, BV));
    expect(await screen.findByText('ABBOTT, ASHLEY')).toBeInTheDocument();
    // a provider with no rates in this plan isn't a pickable row — just a count
    expect(screen.queryByText('NO RATES CLINIC')).not.toBeInTheDocument();
    expect(screen.getByText(/1 more Cardiovascular Disease provider/i)).toBeInTheDocument();
    // no codeless network+specialty distribution scan
    for (const call of api.getRateDistribution.mock.calls) {
      const [code, , , , , spec] = call;
      if (spec) expect(code).toBeTruthy();
    }

    // pick a provider → their menu
    await user.click(screen.getByText('ABBOTT, ASHLEY'));
    expect(await screen.findByText(/procedure menu/i)).toBeInTheDocument();
  });
});
