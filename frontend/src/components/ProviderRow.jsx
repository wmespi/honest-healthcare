import { titleCaseOrg } from '../lib/orgNames';

// Row for one provider in the search dropdown. `disabled` (a provider with no
// rate in the picked plan) renders inert — a listing, not a choice, so "is my
// doctor in this plan?" is still answerable without the dead-end quote screen.
export function ProviderRow({ s, onPick, disabled, planLabel }) {
  const cls = "w-full px-4 py-2.5 text-left border-b border-white/5 last:border-0";
  const inner = (
    <>
      <div className="flex items-center gap-2">
        <span className={`text-sm font-bold truncate ${s.has_rates ? 'text-white' : 'text-slate-500'}`}>{s.name || s.npi}</span>
        {/* min_rate (#87 follow-up): the cheapest in-scope rate this provider
            has, when we know both a service line and a plan — the ranking
            signal the list is actually sorted on, made visible instead of
            just structural. min_rate_is_plausible false means no rate tied
            to this provider's own billing/specialty exists — what's left is
            a network-wide group-fanout floor (issue #14/#73), so it's
            labeled as that instead of read as "her price". */}
        {s.min_rate != null && (
          <span
            className="text-[11px] font-black text-emerald-400 shrink-0"
            title={s.min_rate_is_plausible === false
              ? 'No rate tied to this provider specifically — the network floor for this code family'
              : undefined}
          >
            ${s.min_rate.toFixed(0)}{s.min_rate_is_plausible === false && <sup className="text-slate-500">†</sup>}
          </span>
        )}
        {s.has_rates
          ? <span className="text-[9px] font-black uppercase tracking-wide text-emerald-400 shrink-0">has rates</span>
          : <span className="text-[9px] font-black uppercase tracking-wide text-slate-600 shrink-0">
              {planLabel ? `not in ${planLabel}` : 'no rate data'}
            </span>}
        {s.entity_type === 'organization' && (
          <span className="text-[9px] font-black uppercase tracking-wide text-slate-600 shrink-0">clinic</span>
        )}
      </div>
      <div className={`text-[11px] mt-0.5 truncate ${s.has_rates ? 'text-slate-500' : 'text-slate-600'}`}>
        {[s.specialty, s.group_name && titleCaseOrg(s.group_name), s.city, `NPI ${s.npi}`].filter(Boolean).join(' · ')}
      </div>
    </>
  );
  if (disabled) return <div className={`${cls} opacity-50 cursor-not-allowed`}>{inner}</div>;
  return <button onClick={() => onPick(s)} className={`${cls} hover:bg-white/5 transition-colors`}>{inner}</button>;
}
