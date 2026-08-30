import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';
import App from './App';

vi.mock('./api');

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
  api.getNetworks.mockResolvedValue({ data: [{ network_name: 'GA Blue Value HIX Individual Network', n_rates: 76197 }] });
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
    expect(api.getProviderMenu).toHaveBeenCalledWith('123', undefined, undefined, '', 'plausible');

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
        '99213', 'CPT', undefined, undefined, '123',
      ),
    );
    // With a provider active, the cost card (job 1) is fetched — not the
    // compare-across-providers table.
    await waitFor(() => expect(api.getRateQuote).toHaveBeenCalledWith('99213', 'CPT', '123', undefined));
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

    await waitFor(() => expect(api.getProviderMenu).toHaveBeenCalledWith('123', undefined, undefined, '', 'all'));
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

  it('marks provider search results we hold no rate data for', async () => {
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

    expect(await screen.findByText('has rates')).toBeInTheDocument();
    expect(screen.getByText('no rate data')).toBeInTheDocument();
    expect(screen.getByText(/No rate data — 1 more/i)).toBeInTheDocument();
  });
});

describe('out-of-pocket estimator (issue #30)', () => {
  beforeEach(() => { try { localStorage.clear(); } catch { /* ignore */ } });

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

    await user.click(screen.getByText('Your plan'));
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
    await user.click(screen.getByText('Your plan'));
    await user.type(await screen.findByPlaceholderText('20'), '15');
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem('hh_plan_v1'));
      expect(saved.coinsurance).toBe('15');
    });
  });
});

describe('provider search — specialty mode (issue #31)', () => {
  it('switches to specialty search, picks a specialty, then a provider', async () => {
    const user = userEvent.setup();
    api.searchProviders.mockImplementation((q, specialty) => Promise.resolve({
      data: specialty
        ? [{ npi: 555, name: 'HEART, DR', city: 'ATLANTA', specialty: 'Cardiovascular Disease', has_rates: true, entity_type: 'individual' }]
        : [],
    }));
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await user.click(screen.getByRole('button', { name: 'specialty' }));
    const input = screen.getByPlaceholderText(/e\.g\. cardiology/i);
    await user.click(input);
    await user.type(input, 'cardio');

    await user.click(await screen.findByText('Cardiovascular Disease'));
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', 'Cardiovascular Disease'));
    await user.click(await screen.findByText('HEART, DR'));
    await screen.findByText(/procedure menu/i);
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

    await waitFor(() => expect(api.getProviderMenu).toHaveBeenCalledWith('123', undefined, undefined, 'destruction'));
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
      summary: { min: 56.84, max: 123.08, median: 90.5, n_groups: 12, n_providers: 6566, modal_rate: 56.84, n_at_modal: 9, n_at_or_below_median: 8 },
      results: [
        { provider_group_id: 1, min_rate: 123.08, max_rate: 123.08, median_rate: 123.08, npi_count: 1, is_rollup: false, named_practices: ['MOON DERMATOLOGY'], ga_taxonomies: [], ga_hospital_npis: 0 },
        { provider_group_id: 2, min_rate: 56.84, max_rate: 123.08, median_rate: 90.5, npi_count: 5643, is_rollup: true, named_practices: [], ga_taxonomies: [], ga_hospital_npis: 1 },
      ],
    } });

    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/does the provider matter/i)).toBeInTheDocument();
    // the outlier practice is surfaced individually
    expect(screen.getByText(/Moon Dermatology/i)).toBeInTheDocument();
    // the rollup, which carries the standard schedule, is folded into the summary line
    expect(screen.getByText(/on the standard/i)).toBeInTheDocument();
    expect(api.getRatesByProvider).toHaveBeenCalledWith('99213', 'CPT', undefined, undefined, undefined);
  });
});

describe('default landing state', () => {
  it('loads the network overview without a code or npi', async () => {
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    expect(api.getRateDistribution).toHaveBeenCalledWith(undefined, undefined, undefined, undefined, undefined);
    expect(api.getProviderMenu).not.toHaveBeenCalled();
  });
});
