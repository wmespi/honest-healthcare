import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import App from '../App';
import FindCareMock from './FindCareMock';

// MapLibre needs WebGL, which jsdom doesn't have.
vi.mock('./PriceMap', () => ({ PriceMap: () => null }));

const MRI = '?service=mri-lower-back&loc=30306&plan=blue-value';

const open = (query = '') => render(
  <MemoryRouter initialEntries={[`/mock/find-care${query}`]}>
    <FindCareMock />
  </MemoryRouter>,
);
const rowNames = () => within(screen.getByRole('table'))
  .getAllByRole('button', { expanded: false })
  .map((b) => b.textContent);
const headline = () => screen.getByRole('heading', { level: 1 }).textContent;

async function fillStartCard(user, { service, location, plan }) {
  await user.click(screen.getByLabelText('What do you need?'));
  await user.click(screen.getByRole('option', { name: service }));
  await user.type(screen.getByLabelText('Where are you?'), location);
  if (plan) {
    await user.click(screen.getByLabelText('What plan do you have?'));
    await user.click(screen.getByRole('option', { name: plan }));
  }
  await user.click(screen.getByRole('button', { name: 'See prices' }));
}

describe('find care mock', () => {
  it('is served at /mock/find-care and labelled as sample data', async () => {
    window.history.replaceState(null, '', '/mock/find-care');
    render(<App />);
    expect(await screen.findByText(/know the most you could pay/i)).toBeInTheDocument();
    expect(screen.getByText('Sample data')).toBeInTheDocument();
  });

  it('goes from a service, a ZIP and a plan to the lowest ceiling and a table', async () => {
    const user = userEvent.setup();
    open();
    await fillStartCard(user, { service: /MRI of the lower back/, location: '30306', plan: /Blue Value HMO/ });

    expect(headline()).toMatch(/^\$\d+ is the most you’d pay at /);
    expect(within(screen.getByRole('table')).getByText('Cheapest')).toBeInTheDocument();
    expect(screen.getByLabelText('What plan do you have?')).toHaveValue('Blue Value HMO');
  });

  it('asks for a plan, because prices depend on it', async () => {
    const user = userEvent.setup();
    open();
    await fillStartCard(user, { service: /Colonoscopy/, location: '30306' });
    expect(screen.getByRole('alert')).toHaveTextContent(/your plan/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('asks for a place it can find, and stays on the start card', async () => {
    const user = userEvent.setup();
    open();
    await fillStartCard(user, { service: /Colonoscopy/, location: '90210', plan: /Blue Value HMO/ });
    expect(screen.getByRole('alert')).toHaveTextContent(/metro Atlanta/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('reprices when the plan changes in the header', async () => {
    const user = userEvent.setup();
    open(MRI);
    const before = headline();
    await user.click(screen.getByLabelText('What plan do you have?'));
    await user.click(screen.getByRole('option', { name: /Hartwell Bronze HMO/ }));
    expect(headline()).not.toBe(before);
  });

  it('opens on a tight area in a dense neighborhood and a wider one in a suburb', () => {
    const first = open('?service=mri-lower-back&loc=30316&plan=blue-value');
    expect(screen.getByRole('radio', { name: '5 mi' })).toBeChecked();
    first.unmount();
    open('?service=mri-lower-back&loc=30096&plan=blue-value');
    expect(screen.getByRole('radio', { name: '25 mi' })).toBeChecked();
  });

  it('goes back to the default area when the place changes', async () => {
    const user = userEvent.setup();
    open(`${MRI}&r=50`);
    expect(screen.getByRole('radio', { name: '50 mi' })).toBeChecked();
    const where = screen.getByLabelText('Where are you?');
    await user.clear(where);
    await user.type(where, '30316{Enter}');
    expect(screen.getByRole('radio', { name: '5 mi' })).toBeChecked();
  });

  it('applies a real place when clicking away, and undoes anything else', async () => {
    const user = userEvent.setup();
    open(MRI);
    const where = () => screen.getByRole('combobox', { name: 'Where are you?' });

    await user.clear(where());
    await user.type(where(), '90210');
    await user.tab();
    expect(where()).toHaveValue('30306');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await user.clear(where());
    await user.type(where(), '30096');
    await user.tab();
    expect(where()).toHaveValue('30096');
    expect(screen.getByRole('radio', { name: '25 mi' })).toBeChecked();
  });

  it('reorders the table by distance', async () => {
    const user = userEvent.setup();
    open(MRI);
    expect(rowNames()[0]).not.toBe('Ponce Street Medical Center');
    await user.click(screen.getByRole('button', { name: 'Distance' }));
    expect(rowNames()[0]).toBe('Ponce Street Medical Center');
    expect(screen.getByRole('columnheader', { name: /Distance/ })).toHaveAttribute('aria-sort', 'ascending');
  });

  it('shows more providers within a wider radius', async () => {
    const user = userEvent.setup();
    open(MRI);
    const before = rowNames().length;
    await user.click(screen.getByRole('radio', { name: '25 mi' }));
    expect(rowNames().length).toBeGreaterThan(before);
    expect(screen.getByText('Cheapest within 25 miles')).toBeInTheDocument();
  });

  it('opens a provider to show what makes up the price and how to reach them', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(screen.getByRole('button', { name: 'Beltline Imaging' }));
    expect(screen.getByText(/What makes up/)).toBeInTheDocument();
    expect(screen.getByText('Radiologist’s reading')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Directions/ })).toBeInTheDocument();
    expect(screen.getByText(/310 Krog St NE/)).toBeInTheDocument();
  });

  it('explains the service, with referral advice only on an HMO', async () => {
    const user = userEvent.setup();
    const hmo = open(MRI);
    await user.click(screen.getByRole('button', { name: /What to know about/ }));
    expect(screen.getByText(/Contrast dye/)).toBeInTheDocument();
    expect(screen.getByText(/need a referral/)).toBeInTheDocument();
    hmo.unmount();

    open('?service=mri-lower-back&loc=30306&plan=hartwell-silver');
    await user.click(screen.getByRole('button', { name: /What to know about/ }));
    expect(screen.queryByText(/need a referral/)).not.toBeInTheDocument();
  });

  it('says when a service is usually free as preventive care', () => {
    open('?service=mammogram&loc=30306&plan=blue-value');
    expect(screen.getByText(/usually \$0 in network/)).toBeInTheDocument();
  });

  it('offers a wider search when nothing is nearby', async () => {
    const user = userEvent.setup();
    open('?service=colonoscopy&loc=30096&plan=blue-value&r=5');
    expect(screen.getByText('Nothing within 5 miles')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show within 10 miles' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
  });
});
