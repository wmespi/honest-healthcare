import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../api';
import { BV, CODE_DIST, installDefaultMocks, selectPlan, selectProvider } from '../test-support/explorerFixtures';
import { renderExplorer } from '../test-support/renderExplorer';

vi.mock('../api');

beforeEach(() => installDefaultMocks(api));

// The browse/filter/plan side of the suite: the network-comparison (job 2)
// toggle, the out-of-pocket estimator, the friendly plan picker, the trust
// bar, the specialty filter, the compare-across-providers table (job 3), the
// plan-first gate, and the specialty-first flow. ../Explorer.quote.test.jsx
// covers the cost-card/menu path; ../Explorer.journeys.test.jsx covers the
// deep-link (docs/journeys.md) flows.
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
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
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await selectProvider(user);

    expect(await screen.findByText(/no negotiated rates for/i)).toBeInTheDocument();
    expect(screen.queryByText(/querying mrf data/i)).not.toBeInTheDocument();
  });
});

describe('procedure search scoping', () => {
  it('queries the provider menu (not the global catalog) once a provider is selected', async () => {
    const user = userEvent.setup();
    renderExplorer();
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

    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    const search = screen.getByPlaceholderText(/search procedure or billing code/i);
    await user.click(search);
    await user.type(search, 'office');
    await user.click(await screen.findByText('Office Visit'));

    // plan is already chosen (plan-first flow) — the compare view fills in
    expect(await screen.findByText(/does the provider matter/i)).toBeInTheDocument();
    expect(screen.getByText(/Moon Dermatology/i)).toBeInTheDocument();
    expect(screen.getByText(/on the standard/i)).toBeInTheDocument();
    // #73 — no single provider chosen yet, so this is still the "how is this
    // priced in general" view: histogram stays (only job 1's cost card swaps
    // it for the compact strip)
    expect(screen.getByText(/rate distribution/i)).toBeInTheDocument();
    expect(api.getRatesByProvider).toHaveBeenCalledWith(
      '99213', 'CPT', 'GA Blue Value HIX Individual Network', undefined, undefined,
      expect.objectContaining({ specialty: undefined }));
  });
});

describe('default landing state', () => {
  it('loads the overview for the saved plan, no code or npi', async () => {
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    expect(api.getRateDistribution).toHaveBeenCalledWith(undefined, undefined, BV, undefined, undefined, undefined);
    expect(api.getProviderMenu).not.toHaveBeenCalled();
  });
});

describe('plan-first gate', () => {
  beforeEach(() => { try { localStorage.removeItem('hh_network_v1'); } catch { /* ignore */ } });

  it('blocks the explorer until a plan is chosen, then loads it', async () => {
    const user = userEvent.setup();
    renderExplorer();

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
    renderExplorer();
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
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    // the care step: alphabetical specialty list with provider counts shown
    expect(await screen.findByText(/what kind of care do you need/i)).toBeInTheDocument();
    await user.click(await screen.findByText('Cardiovascular Disease'));

    // provider list for that specialty, scoped to the plan
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', 'Cardiovascular Disease', 40, BV, ''));
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

