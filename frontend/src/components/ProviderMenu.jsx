import { useState } from 'react';
import { ShieldCheck, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { estimateRange } from '../oop';
import { fmt } from '../lib/format';
import { titleCaseOrg } from '../lib/orgNames';
import { EstimateLine } from './EstimateLine';

// The provider "menu" — every procedure the selected provider has a negotiated
// rate for, grouped by RBCS category. Shown when a provider is picked but no
// specific procedure. Clicking a row drills into that procedure.
//
// `codeFilter` (#83) narrows the menu to an exact billing-code allowlist — the
// service-line scope, e.g. the new-patient-visit code family — instead of
// showing everything the provider bills. `codeFilterLabel` names it for copy.
export function ProviderMenu({ data, loading, onPick, providerName, network, onClearNetwork, onShowAll, plan, codeFilter, codeFilterLabel }) {
  const [expanded, setExpanded] = useState(null);

  if (loading) {
    return (
      <div className="mt-2 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Loading this provider’s procedures…
      </div>
    );
  }
  const allRows = data?.results || [];
  const rows = codeFilter ? allRows.filter(r => codeFilter.includes(r.billing_code)) : allRows;
  const who = providerName || 'This provider';

  if (!rows.length) {
    // Narrowed to a service line and the provider has *other* rates, just none
    // in scope — a different message than "no rates at all", with a way to
    // check the group's broader list (it may still carry the code via fan-out).
    if (codeFilter && allRows.length > 0) {
      return (
        <div className="mt-2 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center">
          <p className="text-white font-bold">
            No {codeFilterLabel || 'in-scope'} rate on file for {who}.
          </p>
          <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
            They do have {allRows.length.toLocaleString()} other negotiated rate{allRows.length === 1 ? '' : 's'} —
            just not for this. It may still exist through their billing group.
          </p>
          {onShowAll && data?.tier !== 'all' && (
            <button
              onClick={onShowAll}
              className="mt-4 px-4 py-2 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition-colors"
            >
              Check the group’s full rate list
            </button>
          )}
        </div>
      );
    }
    return (
      <div className="mt-2 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center">
        <p className="text-white font-bold">
          No negotiated rates for {who}{network ? <> in <span className="text-slate-300">{network}</span></> : ''}.
        </p>
        <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
          {data?.provider?.is_hospital || data?.provider?.is_clinic
            ? 'This is a clinic or facility — negotiated rates are contracted to individual providers. Search a provider’s name, or use the “specialty” mode.'
            : network
              ? 'This provider isn’t in that network, or has no published rates there. Try a different network.'
              : 'We don’t hold any published rates for this provider yet.'}
        </p>
        {network && onClearNetwork && (
          <button
            onClick={onClearNetwork}
            className="mt-4 px-4 py-2 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition-colors"
          >
            Search all networks
          </button>
        )}
      </div>
    );
  }

  const byCat = rows.reduce((acc, r) => {
    (acc[r.rbcs_category || 'Other'] ||= []).push(r);
    return acc;
  }, {});
  const cats = Object.entries(byCat).sort((a, b) => b[1].length - a[1].length);

  return (
    <div className="mt-2">
      <div className="mb-4">
        <h2 className="text-white font-black text-xl tracking-tight">
          {data?.provider?.name
            ? `${data.provider.name} — ${codeFilterLabel || 'procedure menu'}`
            : (codeFilterLabel || 'Procedure menu')}
        </h2>
        <p className="text-slate-500 text-xs mt-1">
          {[
            data?.provider?.specialty,
            data?.provider?.group_name && titleCaseOrg(data.provider.group_name),
            data?.provider?.years_in_practice ? `${data.provider.years_in_practice} yrs` : null,
            data?.provider?.address || data?.provider?.city,
          ].filter(Boolean).join(' · ')}
          {(data?.provider?.specialty || data?.provider?.city || data?.provider?.group_name) ? ' · ' : ''}
          {codeFilter
            ? <>{rows.length.toLocaleString()} rate{rows.length === 1 ? '' : 's'} on file. Tap one for the breakdown.</>
            : data?.tier === 'plausible' && data?.group_count > 0
              ? <>{rows.length.toLocaleString()} procedures this provider bills or that are typical for their specialty. Tap one for the breakdown.</>
              : <>{rows.length.toLocaleString()} procedures with a negotiated rate. Tap one for the breakdown.</>}
        </p>
      </div>
      {data?.group_rate_only && (
        <p className="mb-4 text-xs text-amber-300/80 leading-relaxed bg-amber-500/[0.06] border border-amber-500/20 rounded-2xl px-4 py-3">
          Every rate below reaches {providerName || 'this provider'} through a shared billing group — none is
          verified to them individually. Treat these as the group’s rates.
        </p>
      )}
      <div className="space-y-1.5">
        {cats.map(([cat, items]) => (
          <div key={cat} className="rounded-2xl overflow-hidden bg-slate-900 border border-slate-800">
            <button
              onClick={() => setExpanded(e => (e === cat ? null : cat))}
              className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
            >
              <span className="text-sm font-bold text-slate-200">{cat}</span>
              <span className="flex items-center gap-3">
                <span className="text-[11px] text-slate-600 tabular-nums">{items.length}</span>
                <ChevronDown size={15} className={`text-slate-500 transition-transform ${expanded === cat ? 'rotate-180' : ''}`} />
              </span>
            </button>
            <AnimatePresence>
              {expanded === cat && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <div className="divide-y divide-slate-800/60 border-t border-slate-800">
                    {items.map((r, i) => (
                      <button
                        key={i}
                        onClick={() => onPick(r)}
                        className="w-full flex items-center justify-between gap-4 px-5 py-3 text-left hover:bg-indigo-500/[0.06] transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="text-sm text-white font-medium truncate">
                            {r.label || `${r.billing_code_type} ${r.billing_code}`}
                          </div>
                          <div className="text-[11px] text-slate-600 mt-0.5 flex items-center gap-2">
                            <span className="font-mono text-indigo-400">{r.billing_code}</span>
                            {r.is_split && <span className="text-amber-500/80">billed in parts</span>}
                            {r.medicare ? (
                              <span className="flex items-center gap-1 text-emerald-500/90" title={`Billed ${r.medicare.tot_srvcs.toLocaleString()} times to Medicare in ${r.medicare.year}`}>
                                <ShieldCheck size={11} /> Medicare
                              </span>
                            ) : r.tier === 'typical' ? (
                              <span className="text-slate-500" title={`Typical for ${data?.specialty || 'this specialty'} — not verified to this provider`}>
                                typical for specialty
                              </span>
                            ) : r.tier === 'group' ? (
                              <span className="text-slate-600" title="Reaches this provider only via a shared billing group">
                                group rate
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-black text-white tabular-nums">
                            {r.min_rate === r.max_rate ? fmt(r.min_rate) : `${fmt(r.min_rate)}–${fmt(r.max_rate)}`}
                          </div>
                          <EstimateLine className="mt-0.5" est={estimateRange(r.min_rate, r.max_rate, plan, { rbcsCategory: r.rbcs_category })} />
                          <div className="text-[10px] text-slate-600">
                            {r.min_rate !== r.max_rate ? `median ${fmt(r.median_rate)}` : (r.has_global ? 'full procedure' : 'component only')}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
      {/* the unscoped fan-out count ("+10,930 more") is noise once narrowed to
          a service line — we already have in-scope matches above, no reason
          to dangle the rest of this provider's whole billing group */}
      {!codeFilter && data?.tier === 'plausible' && data?.group_count > 0 && onShowAll && (
        <button
          onClick={onShowAll}
          className="mt-3 w-full rounded-2xl border border-dashed border-slate-800 px-5 py-3 text-left text-xs text-slate-500 hover:text-slate-300 hover:border-slate-700 transition-colors"
        >
          + {data.group_count.toLocaleString()} more rates contracted to this provider’s billing
          group — not verified to them individually. <span className="text-indigo-400 font-bold">Show all</span>
        </button>
      )}
      {!codeFilter && data?.tier === 'all' && !data?.group_rate_only && (
        <p className="mt-3 text-[11px] text-slate-600 px-1">
          Showing all contracted rates, including group fan-out. Rows without a badge reach this
          provider only through a shared billing group.
        </p>
      )}
    </div>
  );
}
