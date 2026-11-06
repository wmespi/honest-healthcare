import { describe, it, expect } from 'vitest';
import { displayName, money } from './format';
import { filterResults, pinStack, shareText } from './view';

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
});
