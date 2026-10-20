import { estimate } from '../oop';

const SCREENING_MAMMOGRAM = '77067';

// Which line of a plan's summary of benefits a scan falls under.
export function costShareKind(scan) {
  if (scan.code === SCREENING_MAMMOGRAM) return 'preventive';
  return scan.group === 'CT' || scan.group === 'MRI' ? 'imaging' : 'diagnostic';
}

// What the member would pay of `rate` on `variant` (a sourced plan version),
// or why we cannot say. `deductibleMet` is what the member has already paid.
export function scanShare(variant, scan, rate, deductibleMet = 0) {
  if (!variant || !scan || rate == null) return null;
  const kind = costShareKind(scan);
  if (kind === 'preventive') return { kind: 'preventive' };
  const share = variant[kind];
  if (share.text) return { kind: 'text', text: share.text };
  const e = estimate(rate, {
    deductibleTotal: variant.deductible,
    deductibleMet,
    coinsurance: share.coinsurance,
    oopMax: variant.oop_max,
    oopMet: 0,
    copays: {},
  });
  return e && { kind: 'amount', amount: e.amount, assumption: e.assumption };
}
