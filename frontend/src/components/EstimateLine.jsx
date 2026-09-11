import { fmt } from '../lib/format';

// "You'd pay ≈ $X" line under a rate. `est` is from oop.estimate/estimateRange.
export function EstimateLine({ est, className = '' }) {
  if (!est) return null;
  const amt = est.low != null
    ? (Math.round(est.low) === Math.round(est.high) ? fmt(est.low) : `${fmt(est.low)}–${fmt(est.high)}`)
    : fmt(est.amount);
  return (
    <div className={`text-[11px] text-emerald-300/90 ${className}`}>
      You'd pay ≈ <span className="font-bold text-emerald-200">{amt}</span>
      <span className="text-slate-600"> · {est.assumption}</span>
    </div>
  );
}
