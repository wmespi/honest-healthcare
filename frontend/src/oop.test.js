import { describe, it, expect } from 'vitest';
import { estimate, estimateRange, copayBucket, planIsConfigured } from './oop';

describe('copayBucket', () => {
  it('maps RBCS categories and ER setting', () => {
    expect(copayBucket({ rbcsCategory: 'Imaging' })).toBe('imaging');
    expect(copayBucket({ rbcsCategory: 'E&M' })).toBe('specialist');
    expect(copayBucket({ rbcsCategory: 'Procedure' })).toBe('surgery');
    expect(copayBucket({ rbcsCategory: 'Anesthesia' })).toBe('surgery');
    expect(copayBucket({ rbcsCategory: 'Test' })).toBe(null);
    expect(copayBucket({ rbcsCategory: 'E&M', setting: 'er' })).toBe('emergency');
  });
});

describe('estimate — deductible + coinsurance', () => {
  const plan = { deductibleTotal: 2000, deductibleMet: 0, coinsurance: 20 };

  it('deductible not met, rate below remaining deductible → full rate', () => {
    const r = estimate(300, plan, { rbcsCategory: 'Test' });
    expect(r.amount).toBe(300);
    expect(r.assumption).toMatch(/full rate/);
  });

  it('deductible partly met → remaining deductible + coinsurance on the rest', () => {
    const r = estimate(1000, { deductibleTotal: 2000, deductibleMet: 1800, coinsurance: 20 },
                        { rbcsCategory: 'Test' });
    // $200 to deductible + 20% of $800 = 200 + 160
    expect(r.amount).toBe(360);
  });

  it('deductible met → coinsurance only', () => {
    const r = estimate(1000, { deductibleTotal: 2000, deductibleMet: 2000, coinsurance: 20 },
                        { rbcsCategory: 'Test' });
    expect(r.amount).toBe(200);
    expect(r.assumption).toMatch(/20% coinsurance/);
  });

  it('caps at the out-of-pocket maximum', () => {
    const r = estimate(50000, { deductibleTotal: 2000, deductibleMet: 0, coinsurance: 20,
                                oopMax: 8000, oopMet: 500 }, { rbcsCategory: 'Procedure' });
    expect(r.amount).toBe(7500); // 8000 - 500
  });

  it('returns null with no coinsurance and no copay', () => {
    expect(estimate(300, { deductibleTotal: 2000 }, {})).toBe(null);
  });

  it('guards bad rate input', () => {
    expect(estimate(NaN, plan, {})).toBe(null);
    expect(estimate(-5, plan, {})).toBe(null);
    expect(estimate('300', plan, {})).toBe(null);
  });
});

describe('estimate — copay path', () => {
  const plan = { deductibleTotal: 2000, deductibleMet: 0, coinsurance: 20,
                 copays: { specialist: 50, imaging: 100 } };

  it('flat copay replaces the deductible math for a matching service', () => {
    const r = estimate(4000, plan, { rbcsCategory: 'E&M' });
    expect(r).toEqual({ amount: 50, basis: 'copay', assumption: expect.stringMatching(/copay/) });
  });

  it('imaging copay', () => {
    expect(estimate(1200, plan, { rbcsCategory: 'Imaging' }).amount).toBe(100);
  });

  it('falls back to the deductible path when no copay is set for that bucket', () => {
    const r = estimate(1000, plan, { rbcsCategory: 'Procedure' }); // no surgery copay
    expect(r.basis).not.toBe('copay');
  });

  it('specialist bucket falls back to a primary-care copay', () => {
    const r = estimate(500, { copays: { primary: 30 } }, { rbcsCategory: 'E&M' });
    expect(r.amount).toBe(30);
  });

  it('copay still capped by OOP remaining', () => {
    const r = estimate(500, { copays: { specialist: 50 }, oopMax: 1000, oopMet: 970 },
                        { rbcsCategory: 'E&M' });
    expect(r.amount).toBe(30);
  });
});

describe('estimateRange', () => {
  it('estimates low and high', () => {
    const r = estimateRange(200, 1000, { deductibleTotal: 0, coinsurance: 20 }, { rbcsCategory: 'Test' });
    expect(r).toMatchObject({ low: 40, high: 200 });
  });
  it('null when the plan cannot produce an estimate', () => {
    expect(estimateRange(200, 1000, {}, {})).toBe(null);
  });
});

describe('planIsConfigured', () => {
  it('true with coinsurance', () => expect(planIsConfigured({ coinsurance: 20 })).toBe(true));
  it('true with a copay', () => expect(planIsConfigured({ copays: { specialist: 40 } })).toBe(true));
  it('false when empty', () => expect(planIsConfigured({})).toBe(false));
  it('false with an out-of-range coinsurance and no copays', () =>
    expect(planIsConfigured({ coinsurance: 150 })).toBe(false));
});
