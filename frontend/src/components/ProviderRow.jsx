import { titleCaseOrg } from '../lib/orgNames';

// Two different facts, never one standing in for the other: the CMS MIPS score
// is a quality measure (most PCPs have none); years in practice is
// experience, from the Doctors & Clinicians graduation year. Each renders only
// when it exists, under its own label.
function QualitySignals({ s }) {
  const hasMips = s.mips_score != null;
  const hasYears = s.years_in_practice != null;
  if (!hasMips && !hasYears) return null;
  return (
    <div className="flex items-center gap-3 mt-0.5 text-[11px]">
      {hasMips && (
        <span
          className="text-amber-400 font-bold"
          title={`CMS Merit-based Incentive Payment System score, 0–100${s.mips_year ? `, performance year ${s.mips_year}` : ''}`}
        >
          MIPS {Math.round(s.mips_score)}/100
        </span>
      )}
      {hasYears && (
        <span className="text-slate-400" title="Years since medical-school graduation (CMS Doctors & Clinicians)">
          {s.years_in_practice} yrs in practice
        </span>
      )}
    </div>
  );
}

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
        {s.distance_mi != null && (
          <span className="text-[11px] font-bold text-sky-400 shrink-0" title="Straight-line distance from your ZIP">
            {s.distance_mi.toFixed(1)} mi
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
      <QualitySignals s={s} />
    </>
  );
  if (disabled) return <div className={`${cls} opacity-50 cursor-not-allowed`}>{inner}</div>;
  return <button onClick={() => onPick(s)} className={`${cls} hover:bg-white/5 transition-colors`}>{inner}</button>;
}
