import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import App from '../App';
import FindCare from './FindCare';

vi.mock('../api');
// MapLibre needs WebGL, which jsdom doesn't have.
vi.mock('./PriceMap', () => ({ PriceMap: () => null }));

const SILVER = '45334GA0020002';
const PLANS = [
  { plan_id: SILVER, name: 'Anthem Silver Blue Value HMO 4000', carrier: 'Anthem Blue Cross and Blue Shield',
    needs_referral: false, deductible: 4000 },
  { plan_id: '49046GA0700053', name: 'Anthem Bronze Pathway HMO 6000', carrier: 'Anthem Blue Cross and Blue Shield',
    needs_referral: true, deductible: 6000 },
];
const SERVICES = [
  { service_id: 'mri-lower-back', name: 'Lower-back MRI', keywords: 'lumbar spine', about: 'Magnets and radio waves.',
    could_change: ['Contrast dye'], asks: ['Is this everything?'], preventive_note: null,
    medicare_rate: 180.38, medicare_facility_rate: 243.77 },
  { service_id: 'mammogram-screening', name: 'Screening mammogram', keywords: 'breast', about: 'A low-dose X-ray.',
    could_change: ['Being billed as diagnostic'], asks: ['Will this be billed as a screening?'],
    preventive_note: 'Screening mammograms are usually $0 in network.', medicare_rate: 118.84,
    medicare_facility_rate: null },
];
const MRI = `?service=mri-lower-back&zip=30322&plan=${SILVER}`;

const provider = (npi, name, score = null) => ({ npi, name, specialty: 'Diagnostic Radiology Physician', score });

function place(id, name, kind, distance, parts, extra = {}) {
  const priced = parts.every((p) => p.max !== null);
  return {
    place_id: id, name, kind, address: '1365 CLIFTON RD NE', city: 'ATLANTA', lat: 33.79, lon: -84.32,
    phone: null, stars: null, distance_mi: distance, parts, several_rates: false, billers: [name], providers: [],
    max_rate: priced ? parts.reduce((sum, p) => sum + p.max, 0) : null, ...extra,
  };
}

const EMORY = place('1174811889', 'THE EMORY CLINIC INC', 'practice', 0.3, [{ part: 'all_in', max: 177.21 }], {
  providers: [provider(1417272576, 'ADAM SINGER'), provider(1609226851, 'NINAD SALASTEKAR', 93.2361)],
});
const TRAUB = place('1003000001', 'JEFF A TRAUB, MD, PC', 'practice', 2.7, [{ part: 'all_in', max: 489.04 }], {
  several_rates: true,
});
const GRADY = place('110079', 'GRADY MEMORIAL HOSPITAL', 'hospital', 4.2,
  [{ part: 'facility', max: null }, { part: 'reading', max: 67.07 }], {
    address: '80 JESSE HILL JR DRIVE SE', phone: '(404) 616-1000', stars: 3,
    billers: ['EMORY MEDICAL CARE FOUNDATION', 'RADIOLOGY ASSOCIATES OF DEKALB PC'],
    providers: [provider(1003201336, 'OMAR KALLAS', 95.4)],
  });

function answer(results, extra = {}) {
  const priced = results.filter((p) => p.max_rate !== null).sort((a, b) => a.max_rate - b.max_rate);
  return {
    service_id: 'mri-lower-back', plan_id: SILVER, origin: { zip: '30322', lat: 33.79, lon: -84.32 },
    radius_mi: 5, sort: 'price', cost_sharing: { copay: 300, coinsurance: 50, after_deductible: true },
    results, wider_radius_mi: null, closest_outside: null,
    cheapest_place_id: priced[0]?.place_id ?? null,
    closest_place_id: [...results].sort((a, b) => a.distance_mi - b.distance_mi)[0]?.place_id ?? null,
    median_max_rate: priced.length ? priced[Math.floor(priced.length / 2)].max_rate : null,
    ...extra,
  };
}

const open = (query = '') => render(
  <MemoryRouter initialEntries={[`/find-care${query}`]}>
    <FindCare />
  </MemoryRouter>,
);
const headline = () => screen.getByRole('heading', { level: 1 }).textContent;
const row = (name) => screen.getByRole('button', { name }).closest('tr');

beforeEach(() => {
  vi.resetAllMocks();
  api.getFindCarePlans.mockResolvedValue({ data: PLANS });
  api.getServices.mockResolvedValue({ data: SERVICES });
  api.getFindCare.mockResolvedValue({ data: answer([EMORY, TRAUB, GRADY]) });
});

describe('find care', () => {
  it('is served at /find-care', async () => {
    window.history.replaceState(null, '', '/find-care');
    render(<App />);
    expect(await screen.findByText(/know the most you could pay/i)).toBeInTheDocument();
  });

  it('goes from a scan, a ZIP code and a plan to the cheapest price on the plan', async () => {
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByLabelText('What scan do you need?'));
    await user.click(screen.getByRole('option', { name: 'Lower-back MRI' }));
    await user.type(screen.getByLabelText('What\'s your ZIP code?'), '30322');
    await user.click(screen.getByLabelText('What plan do you have?'));
    await user.click(screen.getByRole('option', { name: /Silver Blue Value/ }));
    await user.click(screen.getByRole('button', { name: 'See prices' }));
    expect(await screen.findByText(/is the most you’d pay at/)).toBeInTheDocument();
    expect(headline()).toBe('$177.21 is the most you’d pay at The Emory Clinic Inc');
    expect(api.getFindCare).toHaveBeenCalledWith(
      { serviceId: 'mri-lower-back', planId: SILVER, zip: '30322', radiusMi: null, sort: 'price' });
    // Once under the answer on a wide screen, and once under the table on a phone.
    expect(screen.getAllByText(/Your plan has a \$4,000 deductible/)).toHaveLength(2);
  });

  it('asks for a five-digit ZIP code and a plan before searching', async () => {
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByLabelText('What scan do you need?'));
    await user.click(screen.getByRole('option', { name: 'Lower-back MRI' }));
    await user.type(screen.getByLabelText('What\'s your ZIP code?'), '3032');
    await user.click(screen.getByRole('button', { name: 'See prices' }));
    expect(screen.getByRole('alert')).toHaveTextContent('five-digit ZIP code');
    await user.clear(screen.getByLabelText('What\'s your ZIP code?'));
    await user.type(screen.getByLabelText('What\'s your ZIP code?'), '30322');
    await user.click(screen.getByRole('button', { name: 'See prices' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Pick your plan');
    expect(api.getFindCare).not.toHaveBeenCalled();
  });

  it('opens a place to show what makes up its price, the member\'s share and who does the scan', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(await screen.findByRole('button', { name: 'The Emory Clinic Inc' }));
    expect(screen.getByText('What makes up $177.21')).toBeInTheDocument();
    expect(screen.getByText(/until you’ve met your \$4,000 deductible. After that, you pay a \$300 copay and then 50% of the rest/))
      .toBeInTheDocument();
    expect(screen.getByText('1365 Clifton Rd NE, Atlanta')).toBeInTheDocument();
    expect(screen.getByText('Ninad Salastekar')).toBeInTheDocument();
    expect(screen.getByText('Score 93')).toBeInTheDocument();
    expect(screen.getByText('No score')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Directions/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /\(\d{3}\)/ })).not.toBeInTheDocument();  // a practice has no phone on file
  });

  it('shows what is known at a hospital, who bills there, and no most you\'d pay', async () => {
    const user = userEvent.setup();
    open(MRI);
    expect(within(await screen.findByRole('table')).getByText('Missing the hospital’s fee')).toBeInTheDocument();
    expect(within(row('Grady Memorial Hospital')).getByText('Incomplete')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Grady Memorial Hospital' }));
    expect(screen.getByText('Not on file')).toBeInTheDocument();
    expect(screen.getByText(/nothing on file confirms the hospital itself is/)).toBeInTheDocument();
    expect(screen.getByText(/Medicare pays hospitals about \$243.77/)).toBeInTheDocument();
    expect(screen.getByText('Emory Medical Care Foundation and Radiology Associates Of Dekalb PC bill for the reading here.'))
      .toBeInTheDocument();
    expect(screen.getByText('Medicare hospital rating: 3 of 5 stars')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /\(404\) 616-1000/ })).toBeInTheDocument();
  });

  it('says to ask which rate applies where the plan lists several', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(await screen.findByRole('button', { name: 'Jeff A Traub, MD, PC' }));
    expect(screen.getByText(/more than one rate here without saying which applies/)).toBeInTheDocument();
  });

  it('finds a place by a provider who works there', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.type(await screen.findByLabelText('Find a place or provider'), 'kallas');
    expect(screen.getByText('Omar Kallas works here')).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 places/)).toBeInTheDocument();
  });

  it('asks the API again for a wider radius or another order', async () => {
    const user = userEvent.setup();
    open(MRI);
    await user.click(await screen.findByRole('radio', { name: '25 mi' }));
    await waitFor(() => expect(api.getFindCare).toHaveBeenLastCalledWith(
      { serviceId: 'mri-lower-back', planId: SILVER, zip: '30322', radiusMi: 25, sort: 'price' }));
    await user.click(screen.getByRole('button', { name: 'Distance' }));
    await waitFor(() => expect(api.getFindCare).toHaveBeenLastCalledWith(
      { serviceId: 'mri-lower-back', planId: SILVER, zip: '30322', radiusMi: 25, sort: 'distance' }));
  });

  it('offers a wider search when nothing is nearby', async () => {
    const user = userEvent.setup();
    api.getFindCare.mockResolvedValue({ data: answer([], {
      closest_outside: { ...GRADY, distance_mi: 34.2 }, wider_radius_mi: 50, closest_place_id: null,
    }) });
    open(`${MRI}&r=5`);
    expect(await screen.findByText('Nothing within 5 miles')).toBeInTheDocument();
    expect(screen.getByText(/The closest is Grady Memorial Hospital, 34.2 mi away, but its full price isn’t on file/))
      .toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show within 50 miles' }));
    await waitFor(() => expect(api.getFindCare).toHaveBeenLastCalledWith(
      { serviceId: 'mri-lower-back', planId: SILVER, zip: '30322', radiusMi: 50, sort: 'price' }));
  });

  it('says so when the plan has no price for the scan anywhere', async () => {
    api.getFindCare.mockResolvedValue({ data: answer([], { radius_mi: 50, closest_place_id: null }) });
    open(MRI);
    expect(await screen.findByText('No prices for this scan on your plan')).toBeInTheDocument();
  });

  it('never names a place with no full price as the answer', async () => {
    api.getFindCare.mockResolvedValue({ data: answer([GRADY], { wider_radius_mi: 10 }) });
    open(MRI);
    expect(await screen.findByText('We can’t say the most you’d pay nearby')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show within 10 miles' })).toBeInTheDocument();
  });

  it('says when it doesn\'t know the ZIP code', async () => {
    api.getFindCare.mockRejectedValue({ response: { status: 404, data: { detail: { code: 'unknown_zip' } } } });
    open(`?service=mri-lower-back&zip=99999&plan=${SILVER}`);
    expect(await screen.findByText('We don’t know the ZIP code 99999')).toBeInTheDocument();
  });

  it('says when a service is usually free as preventive care, with referral advice only when the plan needs one', async () => {
    const user = userEvent.setup();
    api.getFindCare.mockResolvedValue({ data: answer([EMORY], {
      service_id: 'mammogram-screening', cost_sharing: { copay: 0, coinsurance: null, after_deductible: false },
    }) });
    const screening = open(`?service=mammogram-screening&zip=30322&plan=${SILVER}`);
    expect(await screen.findByText('Screening mammograms are usually $0 in network.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /What to know about/ }));
    expect(screen.queryByText(/needs a referral/)).not.toBeInTheDocument();
    screening.unmount();

    open('?service=mammogram-screening&zip=30322&plan=49046GA0700053');
    await user.click(await screen.findByRole('button', { name: /What to know about/ }));
    expect(screen.getByText(/Anthem Bronze Pathway HMO 6000 needs a referral/)).toBeInTheDocument();
  });
});
