import { describe, it, expect } from 'vitest';
import { displayAddress, displayName, displayPerson, money } from './format';
import { filterResults, pinStack, placeView, shareLines, shareText } from './view';

describe('find care wording', () => {
  it('spells names in capitals as words, keeping endings, credentials and short names', () => {
    expect(displayName('EMORY PATIENT CENTERED PRIMARY CARE LLC')).toBe('Emory Patient Centered Primary Care LLC');
    expect(displayName('JEFF A TRAUB, MD, PC')).toBe('Jeff A Traub, MD, PC');
    expect(displayName("O'NEIL-SMITH IMAGING")).toBe("O'Neil-Smith Imaging");
    expect(displayName("SAINT JOSEPH'S MERCY CARE SERVICES INC")).toBe("Saint Joseph's Mercy Care Services Inc");
    expect(displayName("CHILDREN'S HEALTHCARE OF ATLANTA, INC.")).toBe("Children's Healthcare of Atlanta, Inc.");
    expect(displayName('JAMES L MCQUAIG JR OD & A BLAKE HUTTO OD')).toBe('James L McQuaig Jr OD & A Blake Hutto OD');
    expect(displayName('ABIGAIL S. HOLBROOK, MSW, LCSW, LLC')).toBe('Abigail S. Holbrook, MSW, LCSW, LLC');
    expect(displayName('USA VASCULAR CENTER OF DECATUR LLC')).toBe('USA Vascular Center of Decatur LLC');
    expect(displayName('THE CENTER FOR PAIN AND SPINE AT EAST COBB')).toBe('The Center for Pain and Spine at East Cobb');
    expect(displayName('ATLANTA ENT, SINUS AND ALLERGY')).toBe('Atlanta ENT, Sinus and Allergy');
    expect(displayName('NSH GEORGIA LLC')).toBe('NSH Georgia LLC');
    expect(displayName('SGMC HEALTH PT/OT')).toBe('SGMC Health PT/OT');
    expect(displayName('PEACHTREE OB/GYN, P.C.')).toBe('Peachtree OB/GYN, P.C.');
    expect(displayName("ST JOSEPH'S/CANDLER MEDICAL CTR")).toBe("St Joseph's/Candler Medical Ctr");
    expect(displayName('DR JOHN SMITH PHD (FT GORDON)')).toBe('Dr John Smith PhD (Ft Gordon)');
    expect(displayName('Diagnostic Radiology Physician')).toBe('Diagnostic Radiology Physician');
  });

  it('spells addresses and clinicians by their own rules', () => {
    expect(displayAddress('2501 N PATTERSON ST, SUITE 100')).toBe('2501 N Patterson St, Suite 100');
    expect(displayAddress('1365 CLIFTON RD NE')).toBe('1365 Clifton Rd NE');
    expect(displayAddress('40 OAK CT, STE 2')).toBe('40 Oak Ct, Ste 2');
    expect(displayAddress('4000 US HWY 41 N, PO BOX 12')).toBe('4000 US Hwy 41 N, PO Box 12');
    expect(displayAddress('1 W 12TH ST, 3RD FLOOR, SUITE#116, STE 615A')).toBe('1 W 12th St, 3rd Floor, Suite#116, Ste 615A');
    expect(displayAddress('ST SIMONS ISLAND')).toBe('St Simons Island');
    expect(displayAddress('MCDONOUGH')).toBe('McDonough');
    expect(displayPerson('LI NG')).toBe('Li Ng');
    expect(displayPerson('MARY OF THE WOODS, MD')).toBe('Mary Of The Woods, MD');
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
    expect(shareText({ copay: 250, copay_per: 'day', coinsurance: null, after_deductible: false }, 4000)).toBe(
      'You pay a $250 copay a day.');
    expect(shareText({ copay: 500, copay_per: 'stay', coinsurance: 20, after_deductible: false }, 4000)).toBe(
      'You pay a $500 copay for the stay and then 20% of the rest.');
    expect(shareText({ copay: 0, coinsurance: null, after_deductible: false }, 4000)).toBe('You pay nothing.');
    expect(shareText({ copay: 40, coinsurance: 0, after_deductible: true, copay_limited: true }, 9200)).toBe(
      'You pay a $40 copay for the first visits your plan covers before the deductible. After those, you pay the whole rate until you’ve met your $9,200 deductible, and then nothing.');
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

    it('says a practice\'s facility fee is billed elsewhere, not missing', () => {
      const surgeon = bill('surgeon', 'The doctor’s fee', 'professional', 231, 'plan');
      const noFee = bill('facility', 'The facility’s fee', 'institutional', null, null,
        'Plus the facility’s own fee', 'The hospital or surgery center bills its own fee.');
      const practice = placeView(hospital([noFee, surgeon], { kind: 'practice', kind_name: 'Medical practice' }), context);
      expect(practice.missing.billedElsewhere).toBe(true);
      expect(placeView(hospital([noFee, surgeon]), context).missing.billedElsewhere).toBe(false);
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
