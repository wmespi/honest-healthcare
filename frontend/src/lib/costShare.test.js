import { describe, it, expect } from 'vitest';
import { costShareKind, scanShare } from './costShare';

const variant = {
  deductible: 1200, oop_max: 5000,
  diagnostic: { coinsurance: 20 },
  imaging: { coinsurance: 35 },
};
const mri = { code: '72148', group: 'MRI' };
const xray = { code: '71046', group: 'X-ray' };

describe('costShareKind', () => {
  it('puts CT and MRI under imaging, other scans under diagnostic, and a screening mammogram under preventive', () => {
    expect(costShareKind(mri)).toBe('imaging');
    expect(costShareKind({ code: '70450', group: 'CT' })).toBe('imaging');
    expect(costShareKind(xray)).toBe('diagnostic');
    expect(costShareKind({ code: '76856', group: 'Ultrasound' })).toBe('diagnostic');
    expect(costShareKind({ code: '77067', group: 'Breast and bone' })).toBe('preventive');
  });
});

describe('scanShare', () => {
  it('charges the whole price while the deductible is unmet', () => {
    expect(scanShare(variant, mri, 800, 0)).toMatchObject({ kind: 'amount', amount: 800 });
  });

  it('applies the scan’s coinsurance once the deductible is met', () => {
    expect(scanShare(variant, mri, 1000, 1200)).toMatchObject({ amount: 350 });
    expect(scanShare(variant, xray, 1000, 1200)).toMatchObject({ amount: 200 });
  });

  it('splits a price that crosses the deductible', () => {
    expect(scanShare(variant, mri, 1500, 1000).amount).toBeCloseTo(200 + 1300 * 0.35);
  });

  it('never exceeds the out-of-pocket maximum', () => {
    expect(scanShare({ ...variant, oop_max: 300 }, mri, 5000, 0).amount).toBe(300);
  });

  it('passes through a structure it cannot model instead of guessing', () => {
    const v = { ...variant, imaging: { text: '$300/day, then 50% coinsurance' } };
    expect(scanShare(v, mri, 800)).toEqual({ kind: 'text', text: '$300/day, then 50% coinsurance' });
  });

  it('marks a screening mammogram preventive', () => {
    expect(scanShare(variant, { code: '77067', group: 'Breast and bone' }, 300)).toEqual({ kind: 'preventive' });
  });

  it('returns null without a plan version or a price', () => {
    expect(scanShare(undefined, mri, 800)).toBeNull();
    expect(scanShare(variant, mri, null)).toBeNull();
  });
});
