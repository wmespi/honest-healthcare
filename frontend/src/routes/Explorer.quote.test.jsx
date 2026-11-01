import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../api';
import { BV, CODE_DIST, installDefaultMocks, selectProvider } from '../test-support/explorerFixtures';
import { renderExplorer } from '../test-support/renderExplorer';

vi.mock('../api');

beforeEach(() => installDefaultMocks(api));

// The provider "menu" → job 1 cost-card path: picking a provider, drilling into
// a procedure, and everything the cost card conditionally shows (Medicare
// utilization evidence #14, the cross-specialty rollup caveat, the network-
// comparison toggle). ../Explorer.search.test.jsx and ../Explorer.journeys.test.jsx
// cover the rest of the suite.
describe('provider selected without a procedure', () => {
  it('renders the procedure menu and never requests an npi-only distribution', async () => {
    const user = userEvent.setup();
    renderExplorer();
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
    renderExplorer();
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

    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    // collapsed by default (#73) — no eager /rates/by_network call, and the
    // per-network breakdown isn't shown until asked
    const toggle = await screen.findByText(/compare this rate across your other anthem networks/i);
    expect(screen.queryByText(/does your plan matter/i)).not.toBeInTheDocument();
    expect(api.getRatesByNetwork).not.toHaveBeenCalled();

    await user.click(toggle);

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
    renderExplorer();
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
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    expect(await screen.findByText(/billed this to Medicare/i)).toBeInTheDocument();
    expect(screen.getByText(/142 times in 2024/i)).toBeInTheDocument();
  });

  it('shows the Medicare fee-schedule benchmark + real practice identity on the cost card (#61/#62)', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT', npi: 123,
      provider: {
        name: 'ABBOTT, ASHLEY', specialty: 'Family Medicine', city: 'ATLANTA',
        group_name: 'MILLENNIUM PHYSICIAN GROUP OF GEORGIA LLC',
        years_in_practice: 32,
        hospital_affiliations: [
          { ccn: '110003', facility_name: 'Hospital' },
          { ccn: '110025', facility_name: 'Hospital' },
          { ccn: '117076', facility_name: 'Home health agency' },
        ],
      },
      plausibility: 'typical', tier: 'typical',
      medicare_allowed: 86.75, vs_medicare: 0.95,
      headline: { rate: 82.05, max_rate: 82.05, basis: 'global', pos_label: 'Office / telehealth' },
      components: [{ modifier: '', label: 'Full procedure', description: '', settings: [
        { pos_bucket: 'office', pos_label: 'Office / telehealth', min_rate: 82.05, max_rate: 82.05, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    await screen.findByText(/negotiated cost/i);
    // MPFS benchmark
    expect(screen.getByText(/Medicare allows/i)).toBeInTheDocument();
    expect(screen.getByText(/\$86\.75/)).toBeInTheDocument();
    expect(screen.getByText(/0\.95×/)).toBeInTheDocument();
    // DAC identity — real group, years, hospital count (only the 2 'Hospital' rows)
    expect(screen.getByText(/Millennium Physician Group/i)).toBeInTheDocument();
    expect(screen.getByText(/32 yrs in practice/i)).toBeInTheDocument();
    expect(screen.getByText(/2 hospital affiliations/i)).toBeInTheDocument();
    // #73 — the overview histogram/stat-grid is replaced by the compact
    // "where this rate sits" strip once a specific provider + procedure is chosen
    expect(screen.queryByText(/rate distribution/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Median')).not.toBeInTheDocument();
    expect(screen.getByTitle('This rate: $82.05')).toBeInTheDocument();
    expect(screen.getByTitle('Medicare allows $86.75')).toBeInTheDocument();
  });

  // 99213/99214 at real Blue Value providers come back basis:"component" (the
  // rate is stored under a plan modifier like `EP`), not basis:"global" — but
  // it's still the whole visit, so the benchmark must show.
  it('shows the Medicare benchmark on a single-component (non-split) quote', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '99213', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'ABBOTT, ASHLEY', specialty: 'Family Medicine', city: 'ATLANTA' },
      plausibility: 'typical', tier: 'typical',
      medicare_allowed: 86.75, vs_medicare: 0.95,
      headline: { rate: 82.05, max_rate: 82.05, basis: 'component', pos_label: null },
      components: [{ modifier: 'EP', label: 'Modifier EP', description: '', settings: [
        { pos_bucket: 'office', pos_label: 'Any setting', min_rate: 82.05, max_rate: 82.05, negotiated_type: 'fee schedule' },
      ] }],
      is_component_split: false,
    } });
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    await screen.findByText(/negotiated cost/i);
    expect(screen.getByText(/Medicare allows/i)).toBeInTheDocument();
    expect(screen.getByText(/0\.95×/)).toBeInTheDocument();
    // one modified rate is still the whole visit — not "billed only as separate parts"
    expect(screen.getByText(/Full procedure/i)).toBeInTheDocument();
    expect(screen.queryByText(/billed only as separate parts/i)).not.toBeInTheDocument();
  });

  // A genuine professional (-26) + technical (-TC) split: the headline is one
  // part, so comparing it to the whole Medicare allowed would mislead — hide it.
  it('hides the Medicare benchmark on a -26/-TC component-split quote', async () => {
    const user = userEvent.setup();
    api.getRateDistribution.mockResolvedValue({ data: { ...CODE_DIST, billing_code: '73721' } });
    api.getRateQuote.mockResolvedValue({ data: {
      billing_code: '73721', billing_code_type: 'CPT', npi: 123,
      provider: { name: 'ABBOTT, ASHLEY', specialty: 'Radiology', city: 'ATLANTA' },
      plausibility: 'typical', tier: 'typical',
      medicare_allowed: 191.38, vs_medicare: 0.32,
      headline: { rate: 62.01, max_rate: 381.02, basis: 'component', pos_label: null },
      components: [
        { modifier: '26', label: 'Professional', description: '', settings: [
          { pos_bucket: 'office', pos_label: 'Any setting', min_rate: 62.01, max_rate: 62.01, negotiated_type: 'fee schedule' }] },
        { modifier: 'TC', label: 'Technical', description: '', settings: [
          { pos_bucket: 'office', pos_label: 'Any setting', min_rate: 381.02, max_rate: 381.02, negotiated_type: 'fee schedule' }] },
      ],
      is_component_split: true,
    } });
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    await selectProvider(user);
    await screen.findByText(/procedure menu/i);
    await user.click(screen.getByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));

    await screen.findByText(/negotiated cost/i);
    expect(screen.queryByText(/Medicare allows/i)).not.toBeInTheDocument();
    // a real -26/-TC split IS billed as separate parts
    expect(screen.getByText(/billed only as separate parts/i)).toBeInTheDocument();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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

