import { describe, it, expect } from 'vitest';
import { displayName, money } from './format';
import { filterResults, pinStack, placeView, shareLines, shareText } from './view';

describe('find care wording', () => {
  it('spells names in capitals as words, keeping endings, credentials and directions', () => {
    expect(displayName('EMORY PATIENT CENTERED PRIMARY CARE LLC')).toBe('Emory Patient Centered Primary Care LLC');
    expect(displayName('JEFF A TRAUB, MD, PC')).toBe('Jeff A Traub, MD, PC');
    expect(displayName('2501 N PATTERSON ST, SUITE 100')).toBe('2501 N Patterson St, Suite 100');
    expect(displayName("O'NEIL-SMITH IMAGING")).toBe("O'Neil-Smith Imaging");
    expect(displayName('1365 CLIFTON RD NE')).toBe('1365 Clifton Rd NE');
    expect(displayName('Diagnostic Radiology Physician')).toBe('Diagnostic Radiology Physician');
  });

  it('shows cents only when there are some', () => {
    expect(money(177.21)).toBe('$177.21');
    expect(money(177.2)).toBe('$177.20');
    expect(money(4000)).toBe('$4,000');
  });

  it('says what the member pays, and whether the deductible comes first', () => {
    expect(shareText({ copay: 300, coinsurance: 50, after_deductible: true }, 4000)).toBe(
      'You pay the whole rate until you’ve met your $4,000 deductible. After that, you pay a $300 copay and then 50% of the rest.');
    expect(shareText({ copay: null, coinsurance: 40, after_deductible: true }, 6000)).toBe(
      'You pay the whole rate until you’ve met your $6,000 deductible. After that, you pay 40% of the rate.');
    expect(shareText({ copay: 65, coinsurance: null, after_deductible: false }, 4000)).toBe('You pay a $65 copay.');
    expect(shareText({ copay: 0, coinsurance: null, after_deductible: false }, 4000)).toBe('You pay nothing.');
    expect(shareText({ copay: 0, coinsurance: null, after_deductible: true }, 10600)).toBe(
      'You pay the whole rate until you’ve met your $10,600 deductible. After that, you pay nothing.');
  });

  it('leads each share with its label only when there are several', () => {
    const one = { benefit: 'preventive', label: 'Preventive care', copay: 0, coinsurance: null, after_deductible: false };
    const two = { ...one, benefit: 'specialist', label: 'Specialist visit', copay: 60 };
    expect(shareLines([one], 4000)).toEqual([{ key: 'preventive', label: null, text: 'You pay nothing.' }]);
    expect(shareLines([one, two], 4000)).toEqual([
      { key: 'preventive', label: 'Preventive care', text: 'You pay nothing.' },
      { key: 'specialist', label: 'Specialist visit', text: 'You pay a $60 copay.' }]);
  });

  it('finds a place by its name or a provider who works there', () => {
    const results = [
      { name: 'Midtown Imaging', providers: [{ name: 'Pat Okafor' }] },
      { name: 'Grady Memorial Hospital', providers: [{ name: 'Lee Kim' }, { name: 'Pat Prescott' }] },
    ];
    expect(filterResults(results, 'grady').map((r) => r.name)).toEqual(['Grady Memorial Hospital']);
    const [match] = filterResults(results, 'prescott');
    expect(match.matchedProvider).toBe('Pat Prescott');
    expect(match.providers.map((p) => p.name)).toEqual(['Pat Prescott', 'Lee Kim']);
    expect(filterResults(results, 'nobody')).toEqual([]);
  });

  it('stacks pins on the map cheapest on top, with the cheapest result on top of a tie', () => {
    const results = [
      { id: 'practice', maxRate: 180.49, isCheapest: true },
      { id: 'hospital', maxRate: null, isCheapest: false },
      { id: 'clinic', maxRate: 180.49, isCheapest: false },
      { id: 'imaging', maxRate: 447.2, isCheapest: false },
    ];
    expect(pinStack(results).map((r) => r.id)).toEqual(['hospital', 'imaging', 'clinic', 'practice']);
    expect(results.map((r) => r.id)).toEqual(['practice', 'hospital', 'clinic', 'imaging']);
  });

  describe('a hospital\'s own fee', () => {
    const context = { cheapestId: null, closestId: null, median: null, facilityRate: 243.77 };
    const hospital = (parts, extra = {}) => ({
      place_id: '110079', name: 'GRADY MEMORIAL HOSPITAL', kind: 'hospital', kind_name: 'Hospital', address: '80 JESSE HILL JR DRIVE SE',
      city: 'ATLANTA', lat: 33.75, lon: -84.38, phone: null, stars: 3, distance_mi: 4.2, max_rate: null, parts,
      several_rates: false, billers: [], providers: [], cash_price: null, ...extra,
    });
    const bill = (name, label, billingClass, max, source, note = null, explain = null) => ({
      part: name, label, billing_class: billingClass, max, source, missing_note: note, missing_explain: explain,
    });
    const FEE = bill('facility', 'Hospital facility fee', 'institutional', 210, 'hospital');
    const NO_FEE = bill('facility', 'Hospital facility fee', 'institutional', null, null,
      'Missing the hospital’s fee', 'Hospitals bill their own fee.');
    const NO_READING = bill('reading', 'Reading', 'professional', null, null, 'Missing the reading',
      'Ask the hospital who will read yours.');
    const READING = bill('reading', 'Reading', 'professional', 35, 'plan');

    it('offers Medicare\'s rough guide only where the fee is missing', () => {
      const missing = placeView(hospital([NO_FEE, READING]), context);
      expect(missing.facilityEstimate).toBe(243.77);
      expect(missing.missing.rowNote).toBe('Missing the hospital’s fee');
      expect(placeView(hospital([FEE, NO_READING]), context).facilityEstimate).toBeNull();
    });

    it('says the reading is missing when only the reading is', () => {
      const view = placeView(hospital([FEE, NO_READING]), context);
      expect(view.missing.rowNote).toBe('Missing the reading');
      expect(view.missing.explain).toBe('Ask the hospital who will read yours.');
    });

    it('doesn\'t send someone to a hospital for a reading missing anywhere else', () => {
      const own = { ...NO_READING, missing_explain: 'Ask who will read yours.' };
      const view = placeView(hospital([bill('scan', 'Scan', 'professional', 400, 'plan'), own],
        { kind: 'imaging_center', kind_name: 'Imaging center' }), context);
      expect(view.kindLabel).toBe('Imaging center');
      expect(view.missing.rowNote).toBe('Missing the reading');
      expect(view.missing.explain).toBe('Ask who will read yours.');
      expect(view.facilityEstimate).toBeNull();
    });

    it('exposes the cash price and whether the fee is the hospital\'s own', () => {
      const own = placeView(hospital([FEE, NO_READING], { cash_price: 320 }), context);
      expect(own.cashPrice).toBe(320);
      expect(own.feeFromHospital).toBe(true);
      const plan = placeView(hospital([{ ...FEE, max: 165, source: 'plan' }, NO_READING]), context);
      expect(plan.cashPrice).toBeNull();
      expect(plan.feeFromHospital).toBe(false);
    });
  });
});
