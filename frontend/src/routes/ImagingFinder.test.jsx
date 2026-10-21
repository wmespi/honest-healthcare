import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../api';
import { BV, installDefaultMocks } from '../test-support/explorerFixtures';
import { ImagingFinder } from './ImagingFinder';

vi.mock('../api');

const MENU = [
  { code: '70450', label: 'Head CT', group: 'CT' },
  { code: '72148', label: 'Lower-back MRI', group: 'MRI' },
];

const stat = (min, typical, max) => ({ min, typical, max });
const RESULT = {
  billing_code: '70450', network_name: BV, n_practices: 2,
  facility_estimate: { amount: 106.81, source: 'medicare_opps', kind: 'estimate' },
  results: [
    { practice_id: '1', practice_name: 'Marietta Imaging Partners', city: 'Marietta', distance_mi: 4.2, n_providers: 3,
      basis: 'global', typical_rate: 240, global: stat(200, 240, 300), technical: null, professional: null },
    { practice_id: '2', practice_name: 'Northside Radiology', city: 'Atlanta', distance_mi: null, n_providers: 1,
      basis: 'professional_only', typical_rate: 70, global: null, technical: null, professional: stat(70, 70, 70) },
  ],
};

const VARIANT = {
  id: 'v1', network_name: BV, name: 'Gold Test HMO 1200', plan_year: 2026,
  deductible: 1200, oop_max: 5000, diagnostic: { coinsurance: 20 }, imaging: { coinsurance: 35 },
  source: 'https://sbc.anthem.com/x.pdf',
};

beforeEach(() => {
  installDefaultMocks(api);
  api.getServiceLines.mockResolvedValue({ data: { imaging: { menu: MENU } } });
  api.getImagingProviders.mockResolvedValue({ data: RESULT });
  api.getPlanCostSharing.mockResolvedValue({ data: [VARIANT] });
});

const renderPage = () => render(<MemoryRouter><ImagingFinder /></MemoryRouter>);

describe('ImagingFinder', () => {
  it('asks for a plan first when none is saved, and fetches nothing', async () => {
    localStorage.removeItem('hh_network_v1');
    renderPage();
    expect(await screen.findByText(/which plan are you on/i)).toBeInTheDocument();
    expect(api.getImagingProviders).not.toHaveBeenCalled();
  });

  it('prices the chosen scan for the saved plan and explains the number', async () => {
    const user = userEvent.setup();
    renderPage();
    const picker = await screen.findByLabelText('Which scan?');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(picker, '70450');

    await waitFor(() => expect(api.getImagingProviders).toHaveBeenCalledWith('70450', BV, undefined));
    expect(await screen.findByText('Marietta Imaging Partners')).toBeInTheDocument();
    expect(screen.getByText('$240.00')).toBeInTheDocument();
    expect(screen.getByText(/\$200\.00 to \$300\.00 across contracts/)).toBeInTheDocument();
    expect(screen.getByText(/complete price/i)).toBeInTheDocument();
    expect(screen.getByText(/reading only/i)).toBeInTheDocument();
    expect(screen.getByText(/hospital and facility fees are not included/i)).toBeInTheDocument();
  });

  it('labels the hospital fee as a Medicare-based estimate, not an Anthem price', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Which scan?'), '70450');
    const box = (await screen.findByText(/if a hospital does the scan/i)).closest('div');
    expect(box).toHaveTextContent(/estimate/i);
    expect(box).toHaveTextContent(/\$106\.81/);
    expect(box).toHaveTextContent(/not a price anthem has published/i);
  });

  it('sends the ZIP and radius only once the ZIP is five digits', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Which scan?'), '70450');
    await user.type(screen.getByLabelText('Your ZIP code'), '3030');
    expect(api.getImagingProviders).toHaveBeenLastCalledWith('70450', BV, undefined);
    await user.type(screen.getByLabelText('Your ZIP code'), '9');
    await waitFor(() => expect(api.getImagingProviders).toHaveBeenLastCalledWith('70450', BV, { zip: '30309', radius_mi: 25 }));
    expect(await screen.findByText(/4\.2 mi/)).toBeInTheDocument();
  });

  it('shows an error rather than stale results when the request fails', async () => {
    const user = userEvent.setup();
    api.getImagingProviders.mockRejectedValue(new Error('400'));
    renderPage();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Which scan?'), '70450');
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load prices/i);
  });

  it('estimates the member\u2019s share once a plan version is chosen, and cites its source', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Which scan?'), '70450');
    await screen.findByText('Marietta Imaging Partners');
    expect(screen.queryByText(/you.d pay about/i)).not.toBeInTheDocument();

    await user.selectOptions(await screen.findByLabelText('Your plan version'), 'v1');
    expect(screen.getAllByText(/you.d pay about \$240\.00/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: /Gold Test HMO 1200 summary/ })).toHaveAttribute('href', 'https://sbc.anthem.com/x.pdf');

    await user.type(screen.getByLabelText('Deductible met so far'), '1200');
    expect(await screen.findByText(/you.d pay about \$84\.00/i)).toBeInTheDocument();
  });

  it('only says it cannot show a share while no plan version is chosen', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Head CT' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Which scan?'), '70450');
    await screen.findByText('Marietta Imaging Partners');
    expect(screen.getByText(/shows prices only, not your share/i)).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText('Your plan version'), 'v1');
    expect(screen.queryByText(/shows prices only, not your share/i)).not.toBeInTheDocument();
  });
});
