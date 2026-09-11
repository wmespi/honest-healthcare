import { ShieldCheck } from 'lucide-react';
import { estimateRange } from '../oop';
import { fmt } from '../lib/format';
import { titleCaseOrg } from '../lib/orgNames';
import { EstimateLine } from './EstimateLine';
import { RateSitsStrip } from './RateSitsStrip';

// Job 1 — the cost answer for one procedure at one provider. Shows a headline
// rate (a range when it varies by setting) and the breakdown by component
// (full procedure / professional fee / technical fee) and place of service.
export function ProviderCostCard({ data, loading, providerName, plan, rbcsCategory }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Pricing this procedure…
      </div>
    );
  }
  if (!data?.headline) return null;
  const { headline, components, is_component_split, provider, plausibility, tier,
          medicare_utilization: mu, medicare_allowed: mcr, vs_medicare } = data;
  const range = (lo, hi) => (lo === hi ? fmt(lo) : `${fmt(lo)}–${fmt(hi)}`);
  const name = provider?.name || providerName;
  const hospCount = (provider?.hospital_affiliations || []).filter(a => a.facility_name === 'Hospital').length;
  const sub = [
    provider?.specialty,
    provider?.group_name && titleCaseOrg(provider.group_name),
    provider?.years_in_practice ? `${provider.years_in_practice} yrs in practice` : null,
    hospCount ? `${hospCount} hospital affiliation${hospCount > 1 ? 's' : ''}` : null,
    provider?.address || provider?.city,
  ].filter(Boolean).join(' · ');

  // CMS Physician Fee Schedule benchmark (issue #61) — mcr is null until
  // `make mpfs` runs. Anchors the negotiated rate: <1.2× Medicare = in line,
  // higher = worth noticing. `vs_medicare` compares the headline to the *global*
  // Medicare allowed, so it's meaningful when the headline is a whole-procedure
  // figure — a global rate, or a single-component rate that isn't a
  // professional/technical split (an E&M code stored under a plan modifier like
  // `EP` comes back basis:"component" but is still the full visit). Hide it only
  // for a genuine -26/-TC split, where comparing one part to the whole misleads.
  const benchmarkComparable = headline.basis === 'global' || !is_component_split;
  const medicareBenchmark = mcr && benchmarkComparable && (
    <p className={`mt-2 text-[11px] ${vs_medicare > 1.5 ? 'text-amber-400/90' : 'text-slate-500'}`}>
      Medicare allows <span className="font-semibold">{fmt(mcr)}</span> for this
      {vs_medicare ? <> — this plan pays <span className="font-semibold">{vs_medicare}×</span> that</> : null}
    </p>
  );
  // The rate belongs to the billing group, not the individual, when the CMS
  // tier says "group" (no utilization, not typical for the specialty) or the
  // legacy heuristic flags a cross-specialty mismatch.
  const groupRate = tier === 'group' || plausibility === 'unlikely';

  // CMS Medicare Part B evidence (issue #14). mu is null until the utilization
  // file is built; {billed:false} means the file is built but this NPI has no
  // Part B row for this code (weak — <=10-beneficiary rows are dropped, and it
  // misses pediatric / commercial / cash practice). The dollar figure is left to
  // the Medicare benchmark line above — this line is about "do they do it".
  const medicareBilled = mu?.billed && (
    <p className="mt-4 flex items-start gap-2 text-sm text-emerald-300/90 leading-relaxed max-w-lg">
      <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400" />
      <span>
        {name || 'This provider'} billed this to Medicare{' '}
        <span className="font-semibold text-emerald-200">
          {mu.tot_srvcs.toLocaleString()} time{mu.tot_srvcs === 1 ? '' : 's'} in {mu.year}
        </span>
        {' '}— so this is a procedure they actually perform.
      </span>
    </p>
  );

  const breakdown = (
    <div className="space-y-3">
      {components.map(c => (
        <div key={c.modifier || 'global'} className="rounded-2xl bg-slate-950/50 border border-slate-800 p-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm font-bold text-slate-200">{c.label}</span>
            {c.modifier && <span className="text-[10px] font-mono text-slate-600 shrink-0">mod {c.modifier}</span>}
          </div>
          {c.description && <p className="text-[11px] text-slate-500 mt-1">{c.description}</p>}
          <div className="mt-2.5 divide-y divide-slate-800/50">
            {c.settings.map((s, i) => (
              <div key={i} className="flex items-center justify-between py-2 text-sm">
                <span className="text-slate-400">{s.pos_label}</span>
                <span className="text-white font-bold tabular-nums">{range(s.min_rate, s.max_rate)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">
        {groupRate ? 'Group-contracted rate' : 'Negotiated cost'}
        {name ? <> {groupRate ? 'for' : 'at'} <span className="text-indigo-300">{name}</span></> : ''}
      </h2>
      {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}

      {groupRate ? (
        <>
          <p className="mt-4 text-sm text-slate-300 leading-relaxed max-w-lg">
            This rate is attached to the <span className="font-semibold text-white">billing group</span> {name} is
            listed under — in this network that group spans thousands of practices and many specialties.
            The rate sheet doesn&rsquo;t say which providers in the group actually perform this procedure, and we
            have no record of whether {name} bills it. Treat the numbers below as the <em>group&rsquo;s</em> rate,
            not {name}&rsquo;s.
          </p>
          {mu && !mu.billed && (
            <p className="mt-3 text-xs text-slate-500 leading-relaxed max-w-lg">
              No Medicare Part B claims from {name || 'this provider'} for this code in {mu.year} either —
              though that misses pediatric, commercial, and cash practice.
            </p>
          )}
          {medicareBenchmark}
          <details className="mt-4 group">
            <summary className="text-xs font-bold text-slate-500 cursor-pointer hover:text-slate-300 list-none">
              Show the group rate ▸
            </summary>
            <div className="mt-3 opacity-70">{breakdown}</div>
          </details>
        </>
      ) : (
        <>
          <div className="mt-4 mb-2">
            <div className="text-4xl sm:text-5xl font-black text-white tracking-tight">
              {range(headline.rate, headline.max_rate)}
            </div>
            <div className="text-xs text-slate-500 mt-2">
              {headline.basis === 'global'
                ? (headline.pos_label
                    ? <>Full procedure · {headline.pos_label}</>
                    : <>Full procedure — varies by where it’s performed</>)
                : components.length === 1
                  // one rate on file, carrying a modifier (e.g. a Medicaid EPSDT
                  // `EP` line) — it's still the whole procedure, not a part.
                  ? <>Full procedure{headline.pos_label ? <> · {headline.pos_label}</> : null}</>
                  : <>This code is billed only as separate parts — see the breakdown below</>}
            </div>
            <EstimateLine
              className="mt-2"
              est={estimateRange(headline.rate, headline.max_rate, plan, { rbcsCategory })}
            />
            {medicareBenchmark}
            {benchmarkComparable && <RateSitsStrip rate={headline.rate} medicare={mcr} />}
          </div>
          {medicareBilled}
          <div className="mt-6">{breakdown}</div>
          {is_component_split && (
            <p className="text-[11px] text-slate-500 mt-4 leading-relaxed">
              Often billed as two line items — a <span className="text-slate-400">professional fee</span> (the physician’s
              reading) and a <span className="text-slate-400">technical fee</span> (the equipment and facility) — which
              together roughly equal the full rate. Which you’re charged depends on where it’s done and who interprets it.
            </p>
          )}
        </>
      )}
    </div>
  );
}
