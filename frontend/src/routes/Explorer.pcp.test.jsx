import { screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from '../api';
import { BV, installDefaultMocks } from '../test-support/explorerFixtures';
import { renderExplorer } from '../test-support/renderExplorer';

vi.mock('../api');

beforeEach(() => installDefaultMocks(api));

const rows = [
  { npi: 1, name: 'NEAR, NANCY', city: 'ATLANTA', specialty: 'Family Medicine', has_rates: true, entity_type: 'individual',
    min_rate: 80, min_rate_is_plausible: true, distance_mi: 1.24, mips_score: 87.4, mips_year: 2024, years_in_practice: 15 },
  { npi: 2, name: 'FAR, FRED', city: 'DECATUR', specialty: 'Internal Medicine', has_rates: true, entity_type: 'individual',
    min_rate: 95, min_rate_is_plausible: true, distance_mi: 8.9, mips_score: null, years_in_practice: 22 },
  { npi: 3, name: 'BARE, BETH', city: 'ATLANTA', specialty: 'Family Medicine', has_rates: true, entity_type: 'individual',
    min_rate: 99, min_rate_is_plausible: true, distance_mi: null, mips_score: null, years_in_practice: null },
];

describe('/find-care PCP picker (ZIP, distance, quality)', () => {
  it('a 5-digit ZIP re-queries with zip + radius and rows show distance', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: rows });
    renderExplorer({ lockedServiceLine: 'pcp' });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp', undefined));

    const input = screen.getByLabelText('Your ZIP code');
    fireEvent.change(input, { target: { value: '3030' } });
    expect(api.searchProviders).not.toHaveBeenCalledWith('', '', 40, BV, 'pcp', expect.anything());
    fireEvent.change(input, { target: { value: '30309' } });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp', { zip: '30309', radius_mi: 10 }));

    fireEvent.change(screen.getByLabelText('Search radius'), { target: { value: '25' } });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp', { zip: '30309', radius_mi: 25 }));

    expect(await screen.findByText('1.2 mi')).toBeInTheDocument();
    expect(screen.getByText('8.9 mi')).toBeInTheDocument();
    expect(screen.getByText(/ranked on cost, distance and quality/i)).toBeInTheDocument();
  });

  it('shows MIPS and years in practice as separate signals, never one as the other', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}`);
    api.searchProviders.mockResolvedValue({ data: rows });
    renderExplorer({ lockedServiceLine: 'pcp' });

    // NEAR has both; FAR has years only; BARE has neither
    expect(await screen.findByText('MIPS 87/100')).toBeInTheDocument();
    expect(screen.getAllByText(/MIPS \d+\/100/)).toHaveLength(1);
    expect(screen.getByText('15 yrs in practice')).toBeInTheDocument();
    expect(screen.getByText('22 yrs in practice')).toBeInTheDocument();
    expect(screen.getAllByText(/yrs in practice/)).toHaveLength(2);
  });

  it('a ?zip= deep link seeds the search', async () => {
    window.history.replaceState(null, '', `/?plan=${encodeURIComponent(BV)}&zip=30309&radius=25`);
    api.searchProviders.mockResolvedValue({ data: rows });
    renderExplorer({ lockedServiceLine: 'pcp' });
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp', { zip: '30309', radius_mi: 25 }));
  });
});
