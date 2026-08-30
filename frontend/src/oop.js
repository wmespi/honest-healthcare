// Out-of-pocket estimator (issue #30). Pure — no React, no storage.
//
// Standard plan mechanics: the member pays the full negotiated rate until the
// deductible is met, then `coinsurance`% of the rest, all capped at the
// out-of-pocket maximum. A flat copay, when the plan uses one for that kind of
// service, replaces that math.
//
// This is an ESTIMATE. Real adjudication applies rules we don't model: bundling,
// prior auth, out-of-network, separate facility fees, and whether copays count
// toward the deductible / OOP max (plans vary).

export const COPAY_BUCKETS = ['primary', 'specialist', 'imaging', 'surgery', 'emergency'];

export const COPAY_LABELS = {
  primary: 'Primary care visit',
  specialist: 'Specialist visit',
  imaging: 'Imaging (MRI, CT, X-ray)',
  surgery: 'Outpatient surgery / procedure',
  emergency: 'Emergency room',
};

// Map an RBCS category (+ optional setting) to a copay bucket, or null when the
// service is normally cost-shared by coinsurance rather than a flat copay.
export function copayBucket({ rbcsCategory, setting } = {}) {
  const s = (setting || '').toLowerCase();
  if (s === 'er' || s === 'emergency') return 'emergency';
  const c = (rbcsCategory || '').toLowerCase();
  if (c === 'imaging') return 'imaging';
  if (c === 'e&m') return 'specialist'; // can't distinguish PCP from specialist; caller falls back
  if (c === 'procedure' || c === 'anesthesia') return 'surgery';
  return null;
}

const n = (v) => (typeof v === 'number' && isFinite(v) && v >= 0 ? v : 0);

/**
 * @param {number} rate            one negotiated rate
 * @param {object} plan            { deductibleTotal, deductibleMet, coinsurance (0-100),
 *                                   oopMax, oopMet, copays: { [bucket]: amount } }
 * @param {object} ctx             { rbcsCategory, setting }
 * @returns {null | { amount: number, basis: string, assumption: string }}
 *          null when there's nothing to compute from (no coinsurance, no copay).
 */
export function estimate(rate, plan = {}, ctx = {}) {
  if (typeof rate !== 'number' || !isFinite(rate) || rate < 0) return null;

  const copays = plan.copays || {};
  const oopLeft = plan.oopMax ? Math.max(n(plan.oopMax) - n(plan.oopMet), 0) : Infinity;

  // Copay path.
  const bucket = copayBucket(ctx);
  let copay = bucket != null ? copays[bucket] : undefined;
  if (copay == null && bucket === 'specialist') copay = copays.primary;
  if (typeof copay === 'number' && isFinite(copay) && copay >= 0) {
    return { amount: Math.min(copay, oopLeft), basis: 'copay',
             assumption: `${COPAY_LABELS[bucket] || bucket} copay` };
  }

  // Deductible + coinsurance path.
  const coins = plan.coinsurance;
  const hasCoins = typeof coins === 'number' && isFinite(coins) && coins >= 0 && coins <= 100;
  if (!hasCoins) return null;

  const dedLeft = Math.max(n(plan.deductibleTotal) - n(plan.deductibleMet), 0);
  const toDeductible = Math.min(rate, dedLeft);
  const coinsAmt = (rate - toDeductible) * (coins / 100);
  const amount = Math.min(toDeductible + coinsAmt, oopLeft);

  let assumption;
  if (dedLeft <= 0) assumption = `${coins}% coinsurance`;
  else if (dedLeft >= rate) assumption = 'deductible not met — you pay the full rate';
  else assumption = `deductible partly met, then ${coins}%`;

  return { amount, basis: dedLeft > 0 ? 'deductible' : 'coinsurance', assumption };
}

/** Estimate a [lo, hi] rate range. Returns null if `estimate` can't. */
export function estimateRange(lo, hi, plan, ctx) {
  const a = estimate(lo, plan, ctx);
  const b = estimate(hi, plan, ctx);
  if (!a || !b) return null;
  return { low: a.amount, high: b.amount, basis: b.basis, assumption: b.assumption };
}

/** Is there enough in `plan` to produce any estimate? */
export function planIsConfigured(plan = {}) {
  const c = plan.coinsurance;
  if (typeof c === 'number' && c >= 0 && c <= 100) return true;
  return Object.values(plan.copays || {}).some((v) => typeof v === 'number' && v >= 0);
}
