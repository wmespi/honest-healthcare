import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../api';
import { BV, CODE_DIST, MENU, installDefaultMocks, selectProvider } from '../test-support/explorerFixtures';
import { renderExplorer } from '../test-support/renderExplorer';

vi.mock('../api');

beforeEach(() => installDefaultMocks(api));

describe('deep links (docs/journeys.md)', () => {
  it('a ?plan=&npi=&code= URL lands directly on the cost card — no plan gate, no clicking through', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}&npi=123&code=99213`);
    renderExplorer();

    // never shows the gate, and the quote is fetched with the deep-linked npi+code
    expect(screen.queryByText(/start with your plan/i)).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRateQuote).toHaveBeenCalledWith('99213', 'CPT', '123', BV));
    expect(await screen.findByText(/negotiated cost/i)).toBeInTheDocument();
    expect(screen.getAllByText('$82.05').length).toBeGreaterThan(0);
  });

  it('a ?plan=&specialty= URL lands on that specialty’s provider list', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}&specialty=${encodeURIComponent('Cardiovascular Disease')}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 123, name: 'ABBOTT, ASHLEY', city: 'ATLANTA', specialty: 'Cardiovascular Disease', has_rates: true, entity_type: 'individual' },
    ] });
    renderExplorer();

    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', 'Cardiovascular Disease', 40, BV, '', undefined));
    expect(await screen.findByText('ABBOTT, ASHLEY')).toBeInTheDocument();
    expect(screen.queryByText(/what kind of care do you need/i)).not.toBeInTheDocument();
  });

  // #87 — the service line is route-driven (lockedServiceLine), not a
  // ?service_line= query param — see ../App.test.jsx's routing describe block
  // for how /find-care wires that prop up for real.
  it('a locked PCP scope shows the ranked provider list, no free-text specialty UI', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual' },
    ] });
    renderExplorer({ lockedServiceLine: 'pcp' });

    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp', undefined));
    expect(await screen.findByText('BAKER, DAVID')).toBeInTheDocument();
    // appears twice — the filter-row chip and the ranked-list heading
    expect(screen.getAllByText('Primary Care (PCP)').length).toBe(2);
    expect(screen.queryByText(/what kind of care do you need/i)).not.toBeInTheDocument();
    // the free-text specialty picker is hidden while a service line is active
    expect(screen.queryByText('All specialties')).not.toBeInTheDocument();
  });

  // #87 follow-up — with a plan known, the backend ranks the PCP list
  // cheapest-first and returns each provider's min_rate; the list should
  // show that price and say it's ranked on it, not just carry it silently.
  it('shows each PCP\'s price and says the list is ranked cheapest first', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 789, name: 'NG, PRIYA', city: 'ATLANTA', specialty: 'Family Medicine', has_rates: true, entity_type: 'individual', min_rate: 60, min_rate_is_plausible: true },
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual', min_rate: 150, min_rate_is_plausible: true },
    ] });
    renderExplorer({ lockedServiceLine: 'pcp' });

    await waitFor(() => expect(api.searchProviders).toHaveBeenCalled());
    expect(await screen.findByText('$60')).toBeInTheDocument();
    expect(await screen.findByText('$150')).toBeInTheDocument();
    expect(screen.getByText(/cheapest first/i)).toBeInTheDocument();
    // both plausible — no "network floor" footnote
    expect(screen.queryByText(/network's floor rate/i)).not.toBeInTheDocument();
  });

  // #87 follow-up — a real bug found live-testing: a naive cheapest-rate pick
  // can land on Anthem's billing-group fan-out (a rate reachable by thousands
  // of unrelated NPIs) instead of anything tied to that provider. When the
  // backend flags that (min_rate_is_plausible: false), the price must say so
  // rather than reading as "her price".
  it('marks a min_rate that is only a group-fanout floor, not tied to that provider', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual', min_rate: 38, min_rate_is_plausible: false },
    ] });
    renderExplorer({ lockedServiceLine: 'pcp' });

    expect(await screen.findByText('$38')).toBeInTheDocument();
    expect(screen.getByText(/network's floor rate/i)).toBeInTheDocument();
  });

  // Without a plan a rate can't be computed (it's plan-specific) — no price
  // shown, no cheapest-first claim, same order/copy as before this shipped.
  it('shows no price and no cheapest-first claim when there is no plan yet', async () => {
    window.history.replaceState(null, '', '/?bypass=1');
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual', min_rate: null },
    ] });
    renderExplorer({ lockedServiceLine: 'pcp' });

    expect(await screen.findByText('BAKER, DAVID')).toBeInTheDocument();
    expect(screen.queryByText(/cheapest first/i)).not.toBeInTheDocument();
  });

  // The actual bug report: the menu must narrow to the new-patient-visit
  // family once a PCP is picked, not show everything they bill.
  // Real bug from live-testing #87: a deep link that lands directly on a
  // quote (?plan=&code=) while a service line is locked was sending
  // `specialty=pcp` to /rates/distribution -- a taxonomy-code-family slug,
  // not a real NUCC specialty label, which matches nothing and 404s a
  // quote that has real data (confirmed live: CPT 99202 in Blue Value,
  // https://github.com/wmespi/honest-healthcare/pull/92-era report).
  it('never sends the locked service line as a bogus specialty= param', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}&code=99204`);
    api.getRateDistribution.mockResolvedValue({ data: CODE_DIST });
    renderExplorer({ lockedServiceLine: 'pcp' });

    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    for (const call of api.getRateDistribution.mock.calls) {
      expect(call).not.toContain('pcp');
    }
  });

  it('scopes the provider menu to the new-patient-visit codes once a PCP is picked', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual' },
    ] });
    api.getProviderMenu.mockResolvedValue({ data: {
      npi: 456, count: 2, tier: 'plausible', group_count: 10930,
      results: [
        { billing_code: '99204', billing_code_type: 'CPT', label: 'New Patient Visit', rbcs_category: 'E&M', min_rate: 150, median_rate: 180, max_rate: 210, n_rates: 2 },
        { billing_code: '99213', billing_code_type: 'CPT', label: 'Office Visit', rbcs_category: 'E&M', min_rate: 40, median_rate: 80, max_rate: 120, n_rates: 3 },
      ],
    } });
    renderExplorer({ lockedServiceLine: 'pcp' });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalled());
    await user.click(await screen.findByText('BAKER, DAVID'));

    await screen.findByText(/new patient visit/i);
    expect(await screen.findByText('E&M')).toBeInTheDocument();
    await user.click(screen.getByText('E&M'));
    // in-scope code shows...
    expect(await screen.findByText('New Patient Visit')).toBeInTheDocument();
    // ...the out-of-scope 99213 does not, and neither does the unscoped
    // group-fanout "10,930 more" affordance (noise once narrowed)
    expect(screen.queryByText('Office Visit')).not.toBeInTheDocument();
    expect(screen.queryByText(/more rates contracted/i)).not.toBeInTheDocument();
  });

  it('shows a scoped empty state when a PCP has other rates but none for a new-patient visit', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: [
      { npi: 456, name: 'BAKER, DAVID', city: 'ATLANTA', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual' },
    ] });
    api.getProviderMenu.mockResolvedValue({ data: MENU }); // 99213 + 45378 only, no new-patient codes
    renderExplorer({ lockedServiceLine: 'pcp' });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalled());
    await user.click(await screen.findByText('BAKER, DAVID'));

    expect(await screen.findByText(/no new patient visit rate on file/i)).toBeInTheDocument();
    expect(screen.getByText(/2 other negotiated rates/i)).toBeInTheDocument();
    expect(screen.getByText(/check the group.s full rate list/i)).toBeInTheDocument();
  });

  it('a ?bypass=1 URL skips the plan gate straight to the no-plan overview (J4)', async () => {
    try { localStorage.removeItem('hh_network_v1'); } catch { /* ignore */ }
    window.history.replaceState(null, '', '/?bypass=1');
    renderExplorer();

    expect(screen.queryByText(/start with your plan/i)).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalledWith(
      undefined, undefined, undefined, undefined, undefined, undefined));
  });

  it('keeps the address bar in sync as the selection changes, for copy-paste sharing', async () => {
    const user = userEvent.setup();
    renderExplorer();
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());

    await selectProvider(user);
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('npi')).toBe('123'));
    expect(new URLSearchParams(window.location.search).get('plan')).toBe(BV);

    await user.click(await screen.findByText('Evaluation & Management'));
    await user.click(await screen.findByText('Office Visit'));
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('code')).toBe('99213'));
  });
});
