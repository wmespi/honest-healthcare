import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import App from '../App';
import FindCareMock from './FindCareMock';
import { LOCATIONS } from './sampleData';

// MapLibre needs WebGL, which jsdom doesn't have.
vi.mock('../findCare/PriceMap', () => ({ PriceMap: () => null }));

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
  await user.click(screen.getByLabelText('What scan do you need?'));
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

  it('goes from a scan, a ZIP and a plan to the lowest ceiling and a table', async () => {
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
    await fillStartCard(user, { service: /CT of the abdomen/, location: '30306' });
    expect(screen.getByRole('alert')).toHaveTextContent(/your plan/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('asks for a place it can find, and stays on the start card', async () => {
    const user = userEvent.setup();
    open();
    await fillStartCard(user, { service: /CT of the abdomen/, location: '90210', plan: /Blue Value HMO/ });
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

  it('shows more places within a wider radius', async () => {
    const user = userEvent.setup();
    open(MRI);
    const before = rowNames().length;
    await user.click(screen.getByRole('radio', { name: '25 mi' }));
    expect(rowNames().length).toBeGreaterThan(before);
    expect(screen.getByText('Cheapest within 25 miles')).toBeInTheDocument();
  });

  it('opens a place to show what makes up the price and how to reach it', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(screen.getByRole('button', { name: 'Beltline Imaging' }));
    expect(screen.getByText(/What makes up/)).toBeInTheDocument();
    expect(screen.getByText('Radiologist’s reading')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Directions/ })).toBeInTheDocument();
    expect(screen.getByText(/310 Krog St NE/)).toBeInTheDocument();
  });

  it('shows the range only at a place with more than one rate', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(screen.getByRole('button', { name: 'Beltline Imaging' }));
    expect(screen.queryByText('Rates on file here')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Clearpath Radiology' }));
    expect(screen.getByText('Rates on file here')).toBeInTheDocument();
    expect(screen.getByText('Lowest')).toBeInTheDocument();
    expect(screen.getByText('Highest')).toBeInTheDocument();
    expect(screen.getByText(/Clearpath Radiology and Midtown Radiology Partners bill here/)).toBeInTheDocument();
  });

  it('lists the doctors at a place, and says what the score is', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(screen.getByRole('button', { name: 'Beltline Imaging' }));
    const { doctors } = LOCATIONS.find((l) => l.id === 'img-beltline');
    for (const doctor of doctors) expect(screen.getByText(doctor.name)).toBeInTheDocument();
    expect(screen.getByText(/isn’t a patient review/)).toBeInTheDocument();
    expect(screen.queryByText(/Medicare hospital rating/)).not.toBeInTheDocument();
  });

  it('shortens a long doctor list, and gives a hospital its rating', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(screen.getByRole('button', { name: 'Ponce Street Medical Center' }));
    expect(screen.getByText('Medicare hospital rating: 4 of 5 stars')).toBeInTheDocument();

    const { doctors } = LOCATIONS.find((l) => l.id === 'hos-ponce');
    const last = doctors[doctors.length - 1].name;
    expect(screen.queryByText(last)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: `Show all ${doctors.length} doctors` }));
    expect(screen.getByText(last)).toBeInTheDocument();
  });

  it('finds a place by a doctor who works there', async () => {
    const user = userEvent.setup();
    open(MRI);
    const { doctors } = LOCATIONS.find((l) => l.id === 'img-beltline');
    await user.type(screen.getByRole('searchbox', { name: 'Find a place or doctor' }), doctors[1].name);
    expect(rowNames()).toEqual(['Beltline Imaging']);
    expect(screen.getByText(`${doctors[1].name} works here`)).toBeInTheDocument();
    expect(screen.getByText(/^1 of \d+ places?$/)).toBeInTheDocument();
  });

  it('says when no place or doctor matches', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.type(screen.getByRole('searchbox', { name: 'Find a place or doctor' }), 'nobody');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByText(/matches “nobody”/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument();
  });

  it('includes an orthopedic practice for a knee scan, but not a back scan', () => {
    const knee = open('?service=knee-xray&loc=30306&plan=blue-value&r=10');
    expect(rowNames()).toContain('Howell Mill Orthopedics');
    knee.unmount();
    open(`${MRI}&r=10`);
    expect(rowNames()).not.toContain('Howell Mill Orthopedics');
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

  it('shows what is known at a hospital, and no most you\'d pay', async () => {
    const user = userEvent.setup();
    open(MRI);
    const row = screen.getByRole('button', { name: 'Ponce Street Medical Center' }).closest('tr');
    expect(within(row).getByText('Incomplete')).toBeInTheDocument();
    expect(within(row).getByText('Missing the hospital’s fee')).toBeInTheDocument();
    expect(within(row).getByText(/^\$\d+ known so far$/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Ponce Street Medical Center' }));
    expect(screen.getByText('What we know so far')).toBeInTheDocument();
    expect(screen.getByText('Not on file')).toBeInTheDocument();
    expect(screen.getByText('Not known')).toBeInTheDocument();
    expect(screen.getByText(/nothing on file confirms the hospital itself is/)).toBeInTheDocument();
    expect(screen.getByText(/Medicare pays hospitals about \$\d+/)).toBeInTheDocument();
    expect(screen.getByText(/Midtown Radiology Partners, and Atlanta Diagnostic Physicians bill here/)).toBeInTheDocument();
    expect(screen.queryByText('Rates on file here')).not.toBeInTheDocument();
  });

  it('says why a radiology group with no office billing has no full price', async () => {
    const user = userEvent.setup();
    open(MRI);
    const row = screen.getByRole('button', { name: 'Midtown Radiology Partners' }).closest('tr');
    expect(within(row).getByText('Missing the cost of the scan')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Midtown Radiology Partners' }));
    expect(screen.getByText(/reading scans done at hospitals/)).toBeInTheDocument();
    expect(screen.queryByText(/Medicare pays hospitals/)).not.toBeInTheDocument();
  });

  it('never names a place with no full price as the answer, and lists it last by price', () => {
    open(MRI);
    expect(headline()).not.toMatch(/Medical Center|Hospital|Radiology Partners/);
    const names = rowNames();
    expect(names[names.length - 1]).toMatch(/Medical Center|Hospital|Radiology/);
    expect(screen.getByText(/comes last when sorted by price/)).toBeInTheDocument();
  });

  it('says so when nothing nearby has a full price, and offers the closest that does', async () => {
    const user = userEvent.setup();
    open('?service=mri-lower-back&loc=30075&plan=blue-value&r=5');
    expect(headline()).toBe('We can’t say the most you’d pay nearby');
    expect(screen.getByText('No full price within 5 miles')).toBeInTheDocument();
    expect(rowNames()).toEqual(['Holcomb Bridge Hospital']);
    expect(screen.queryByText(/Median is the middle/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show within 10 miles' }));
    expect(headline()).toMatch(/^\$\d+ is the most you’d pay at /);
  });

  it('makes no price claim when the closest place outside the radius has no full price', () => {
    open('?service=mri-lower-back&loc=30215&plan=blue-value&r=5');
    expect(screen.getByText('Nothing within 5 miles')).toBeInTheDocument();
    expect(screen.getByText(/Jonesboro Road Medical Center.*but its full price isn’t on file/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show within 25 miles' })).toBeInTheDocument();
  });

  it('offers a wider search when nothing is nearby', async () => {
    const user = userEvent.setup();
    open('?service=mri-lower-back&loc=30114&plan=blue-value&r=5');
    expect(screen.getByText('Nothing within 5 miles')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show within 25 miles' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
  });
});
