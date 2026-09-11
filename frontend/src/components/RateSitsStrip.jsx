import { fmt } from '../lib/format';

// "Where this rate sits" — a one-row replacement for the full histogram (#73)
// on the single-provider cost answer: this rate plotted against the Medicare
// benchmark on a shared scale, so the vs_medicare ratio the text already states
// gets a glanceable magnitude too. (Not a network min→max strip — the
// distribution fetched alongside a chosen provider is npi-scoped, not
// network-wide, so it can't honestly stand in for "how this compares to
// everyone else.")
export function RateSitsStrip({ rate, medicare }) {
  if (rate == null || medicare == null || rate <= 0 || medicare <= 0) return null;
  const scale = Math.max(rate, medicare) * 1.15;
  const pct = (v) => Math.max(0, Math.min(100, (v / scale) * 100));
  const youPct = pct(rate);
  const mcrPct = pct(medicare);
  return (
    <div className="mt-3 max-w-lg">
      <div className="relative h-2 rounded-full bg-slate-800">
        <div className="absolute inset-y-0 left-0 rounded-full bg-indigo-500/30" style={{ width: `${youPct}%` }} />
        <div
          className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3.5 bg-emerald-400/90 rounded-full"
          style={{ left: `${mcrPct}%` }}
          title={`Medicare allows ${fmt(medicare)}`}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white border-2 border-indigo-500"
          style={{ left: `calc(${youPct}% - 6px)` }}
          title={`This rate: ${fmt(rate)}`}
        />
      </div>
      <div className="flex justify-between mt-1.5 text-[10px] text-slate-600 tabular-nums">
        <span>$0</span>
        <span className="text-slate-400 normal-case font-sans">this rate · Medicare</span>
        <span>{fmt(scale)}</span>
      </div>
    </div>
  );
}
