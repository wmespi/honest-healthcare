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
  api.getProviderMenu.mockResolvedValue({ data: MENU });
  api.searchProviders.mockResolvedValue({
    data: [{ npi: 123, name: 'ABBOTT, ASHLEY', city: 'ATLANTA', taxonomy_group: 'Family Medicine', has_rates: true }],
  });
});

async function selectProvider(user) {
  const input = screen.getByPlaceholderText(/search provider or npi/i);
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

    // The menu view loads (no network selected in this scenario — matches the
    // "All Networks" repro).
    expect(await screen.findByText(/procedure menu/i)).toBeInTheDocument();
    expect(api.getProviderMenu).toHaveBeenCalledWith('123', undefined, undefined);

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
    // Provider-rate table is fetched for the drilled-in code.
    expect(api.getRatesByProvider).toHaveBeenCalled();
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

describe('default landing state', () => {
  it('loads the network overview without a code or npi', async () => {
    render(<App />);
    await waitFor(() => expect(api.getRateDistribution).toHaveBeenCalled());
    expect(api.getRateDistribution).toHaveBeenCalledWith(undefined, undefined, undefined, undefined, undefined);
    expect(api.getProviderMenu).not.toHaveBeenCalled();
  });
});
