import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ResultsTable } from './ResultsTable';
import { UnmeasuredNote } from './Quality';
import { placeView } from './view';

vi.mock('../api');

const measure = (over = {}) => ({
  measure: 'OP-32',
  name: 'Hospital visits within 7 days after a colonoscopy',
  about: 'Lower is better.',
  shown: '12.1 per 1,000',
  compared: 'No different from the national rate',
  period: '2022 to 2024',
  source: 'CMS hospital outpatient measures',
  ...over,
});

const volume = measure({ measure: 'volume-colonoscopy', name: 'Colonoscopies done', shown: '101 to 200', compared: null });

const place = (quality, providers = []) => placeView({
  place_id: 'p1', name: 'EXAMPLE HOSPITAL', kind: 'hospital', kind_name: 'Hospital', city: 'ATLANTA', address: '1 MAIN ST',
  phone: null, stars: null, lat: 33, lon: -84, distance_mi: 2, max_rate: 500, parts: [], several_rates: false, billers: [], providers, quality,
}, { cheapestId: null, closestId: null, median: 500, facilityRate: null });

const show = (location, selectedId = null) => render(
  <ResultsTable
    results={[location]}
    plan={{ deductible: 0 }}
    costSharing={{ benefit: 'x', copay: 0, coinsurance: 0, after_deductible: false }}
    sort="price"
    layoutKey="k"
    selectedId={selectedId}
    highlightId={null}
    onSort={() => {}}
    onSelect={() => {}}
    onHover={() => {}}
  />,
);

describe('quality', () => {
  it('shows the first measure under the address', () => {
    show(place([measure(), volume]));
    expect(screen.getByText(/Hospital visits within 7 days after a colonoscopy:/)).toBeInTheDocument();
    expect(screen.getByText('12.1 per 1,000')).toBeInTheDocument();
    expect(screen.queryByText(/Colonoscopies done/)).not.toBeInTheDocument();
  });

  it('lists every measure with its period and source in the detail', () => {
    show(place([measure(), volume]), 'p1');
    expect(screen.getByText('Colonoscopies done:')).toBeInTheDocument();
    expect(screen.getAllByText(/2022 to 2024, CMS hospital outpatient measures/).length).toBe(2);
  });

  it('shows a clinician’s volume', () => {
    show(place([], [{ npi: '1', name: 'DR ONE', specialty: 'Gastroenterology', score: null, quality: [volume] }]), 'p1');
    expect(screen.getByText('Colonoscopies done: 101 to 200')).toBeInTheDocument();
  });

  it('shows no line for a place without measures', () => {
    show(place([]));
    expect(screen.queryByText(/per 1,000|compared/)).not.toBeInTheDocument();
    expect(screen.queryByText('Quality')).not.toBeInTheDocument();
  });

  it('says when no public measure covers the care', () => {
    render(<UnmeasuredNote />);
    expect(screen.getByText(/No public quality measure covers this kind of care/)).toBeInTheDocument();
  });
});
