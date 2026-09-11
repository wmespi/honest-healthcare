import { Building2 } from 'lucide-react';
import { fmt } from '../lib/format';
import { providerLabel } from '../lib/orgNames';

// "Does the provider matter?" — Job 3. Blue Value is close to a network-wide fee
// schedule, so for most codes every provider negotiated the same rate and the
// honest answer is "provider choice doesn't change the price". When rates do
// vary, show the ranked list with the named practices surfaced.
export function ProviderRateTable({ data, loading, specialty }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Comparing providers…
      </div>
    );
  }
  const rows = data?.results || [];
  if (!rows.length) return null;
  const s = data.summary || {};

  const medians = rows.map(r => r.median_rate).filter(x => x != null);
  const lo = Math.min(...medians), hi = Math.max(...medians);
  const uniform = medians.length > 1 && hi - lo < Math.max(0.02 * lo, 1);
  const rangeStr = s.min === s.max ? fmt(s.min) : `${fmt(s.min)}–${fmt(s.max)}`;

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">
        Does the provider matter?{specialty && <span className="text-indigo-300 font-bold text-base"> · {specialty}</span>}
      </h2>

      {uniform ? (
        <div className="mt-4">
          <div className="text-3xl sm:text-4xl font-black text-white tracking-tight">{rangeStr}</div>
          <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
            Every in-network provider negotiated <span className="text-white font-semibold">the same rate</span> for
            this procedure.{' '}
            {s.min !== s.max && 'The spread is office vs. facility setting — not one provider vs. another. '}
            Picking a cheaper clinic won’t lower this price.
          </p>
          <p className="text-slate-600 text-xs mt-3">
            {(s.n_practices ?? 0).toLocaleString()} billing practices · {(s.n_providers ?? 0).toLocaleString()} providers
          </p>
        </div>
      ) : (() => {
        // "Typical" = a contract carrying the full standard schedule (same floor
        // and ceiling as the network). Outliers miss the cheap tier or are
        // capped differently — those are the only actionable rows.
        const isTypical = r => r.min_rate === s.min && r.max_rate === s.max;
        const atModal = rows.filter(isTypical);
        const outliers = rows.filter(r => !isTypical(r));
        const Row = ({ r }) => (
          <div className="flex items-center justify-between gap-4 py-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Building2 size={13} className="text-slate-600 shrink-0" />
                <span className="text-sm text-white font-medium truncate">{providerLabel(r)}</span>
              </div>
              <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-x-2.5 flex-wrap">
                <span className="tabular-nums">{(r.npi_count || 0).toLocaleString()} providers</span>
                {r.ga_hospital_npis > 0 && <span className="text-amber-400">hospital-affiliated</span>}
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="text-lg font-black text-white tabular-nums">
                {r.min_rate === r.max_rate ? fmt(r.min_rate) : `${fmt(r.min_rate)}–${fmt(r.max_rate)}`}
              </div>
              {r.min_rate !== r.max_rate && <div className="text-[10px] text-slate-600">by setting</div>}
            </div>
          </div>
        );
        return (
          <>
            <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
              At most in-network providers this is <span className="text-white font-semibold">{rangeStr}</span>
              {' '}(depending on setting).
              {outliers.length > 0
                ? <> {outliers.length} contract{outliers.length > 1 ? 's differ' : ' differs'}:</>
                : <> Every contract we hold uses that same schedule.</>}
            </p>
            {outliers.length > 0 && (
              <div className="mt-4 divide-y divide-slate-800/60">
                {outliers.map((r, i) => <Row key={i} r={r} />)}
              </div>
            )}
            {atModal.length > 0 && (
              <p className="text-slate-600 text-[11px] mt-4">
                {atModal.length} contract{atModal.length > 1 ? 's' : ''} on the standard {rangeStr} schedule
                {atModal.some(r => r.practice_name) && (
                  <> — incl. {atModal.filter(r => r.practice_name).slice(0, 3).map(providerLabel).join(', ')}</>
                )}
              </p>
            )}
          </>
        );
      })()}
    </div>
  );
}
