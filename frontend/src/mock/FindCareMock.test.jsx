import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import App from '../App';
import FindCareMock from './FindCareMock';

// MapLibre needs WebGL, which jsdom doesn't have.
vi.mock('./PriceMap', () => ({ PriceMap: () => null }));

const open = (query = '') => render(
  <MemoryRouter initialEntries={[`/mock/find-care${query}`]}>
    <FindCareMock />
  </MemoryRouter>,
);
const rowNames = () => screen.getAllByRole('button', { expanded: false }).map((b) => b.textContent);

describe('find care mock', () => {
  it('is served at /mock/find-care and labelled as sample data', async () => {
    window.history.replaceState(null, '', '/mock/find-care');
    render(<App />);
    expect(await screen.findByText(/find the cheapest care near you/i)).toBeInTheDocument();
    expect(screen.getByText('Sample data')).toBeInTheDocument();
  });

  it('goes from a service and a ZIP to the cheapest provider and a table', async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByLabelText('What do you need?'));
    await user.click(screen.getByRole('option', { name: /MRI of the lower back/ }));
    await user.type(screen.getByLabelText('Where are you?'), '30306');
    await user.click(screen.getByRole('button', { name: 'See prices' }));

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/^\$\d+ at Red Clay Imaging$/);
    expect(within(screen.getByRole('table')).getByText('Cheapest')).toBeInTheDocument();
    expect(screen.getByLabelText('What do you need?')).toHaveValue('MRI of the lower back');
  });

  it('asks for a place it can find, and stays on the start card', async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByLabelText('What do you need?'));
    await user.click(screen.getByRole('option', { name: /Colonoscopy/ }));
    await user.type(screen.getByLabelText('Where are you?'), '90210');
    await user.click(screen.getByRole('button', { name: 'See prices' }));

    expect(screen.getByRole('alert')).toHaveTextContent(/metro Atlanta/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('reorders the table by distance', async () => {
    const user = userEvent.setup();
    open('?service=mri-lower-back&loc=30306');
    expect(rowNames()[0]).toBe('Red Clay Imaging');
    await user.click(screen.getByRole('button', { name: 'Distance' }));
    expect(rowNames()[0]).toBe('Ponce Street Medical Center');
    expect(screen.getByRole('columnheader', { name: /Distance/ })).toHaveAttribute('aria-sort', 'ascending');
  });

  it('shows more providers within a wider radius', async () => {
    const user = userEvent.setup();
    open('?service=mri-lower-back&loc=30306');
    const before = rowNames().length;
    await user.click(screen.getByRole('radio', { name: '25 mi' }));
    expect(rowNames().length).toBeGreaterThan(before);
    expect(screen.getByText('Cheapest within 25 miles')).toBeInTheDocument();
  });

  it('opens a provider to show how to reach them', async () => {
    const user = userEvent.setup();
    open('?service=mri-lower-back&loc=30306');
    await user.click(screen.getByRole('button', { name: 'Beltline Imaging' }));
    expect(screen.getByRole('link', { name: /Directions/ })).toBeInTheDocument();
    expect(screen.getByText(/310 Krog St NE/)).toBeInTheDocument();
  });

  it('offers a wider search when nothing is nearby', async () => {
    const user = userEvent.setup();
    open('?service=colonoscopy&loc=30096&r=5');
    expect(screen.getByText('Nothing within 5 miles')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show within 10 miles' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
  });
});
