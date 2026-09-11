import { ProviderRow } from './ProviderRow';

// Ranked provider list for a specialty (or a service line, #83) within the
// selected plan — the step between "pick your care" and a specific provider.
// Rated providers first (that ranking is the endpoint's job). `label` overrides
// the display text when the scope isn't a free-text specialty (e.g. "Primary
// Care (PCP)" for a service-line scope) — `specialty` alone still works as before.
export function SpecialtyProviderList({ specialty, label, providers, loading, onPick }) {
  const display = label || specialty;
  if (loading) {
    return (
      <div className="mt-6 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Finding {display} providers…
      </div>
    );
  }
  const rows = providers || [];
  if (!rows.length) {
    return (
      <div className="mt-6 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center">
        <p className="text-white font-bold">No {display} providers with rates in this plan.</p>
        <p className="text-slate-500 text-sm mt-2">Try another specialty, or search a provider by name.</p>
      </div>
    );
  }
  const withRates = rows.filter(r => r.has_rates);
  const noRates = rows.filter(r => !r.has_rates);
  // #87 follow-up — the backend only returns min_rate (and sorts on it) once
  // it knows both a service line and a plan; when it does, say so, rather
  // than leaving "ranked cheapest first" as an unstated fact about the order.
  const rankedByCost = withRates.some(r => r.min_rate != null);
  const hasFloorRate = withRates.some(r => r.min_rate != null && r.min_rate_is_plausible === false);
  const pick = (s) => onPick(String(s.npi), s.name || String(s.npi));
  return (
    <div className="mt-6 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">{display}</h2>
      {withRates.length > 0 ? (
        <>
          <p className="text-slate-500 text-xs mt-1">
            {withRates.length.toLocaleString()} provider{withRates.length === 1 ? '' : 's'} with negotiated
            rates in your plan{rankedByCost ? ', cheapest first' : ''}. Pick one to see what they charge.
          </p>
          <div className="mt-4 divide-y divide-slate-800/60">
            {withRates.map((s, i) => <ProviderRow key={i} s={s} onPick={pick} />)}
          </div>
          {hasFloorRate && (
            <p className="text-slate-600 text-[11px] mt-3">
              † no rate tied to that provider specifically — the network's floor rate for this kind of visit.
            </p>
          )}
        </>
      ) : (
        <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
          None of the {display} providers we can see have published rates in this plan.
          Try a related specialty, or search a provider by name.
        </p>
      )}
      {noRates.length > 0 && (
        <p className="text-slate-600 text-[11px] mt-4">
          + {noRates.length.toLocaleString()} more {display} provider{noRates.length === 1 ? '' : 's'} in
          NPPES we hold no rate data for in this plan.
        </p>
      )}
    </div>
  );
}
