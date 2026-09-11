import { ChevronDown } from 'lucide-react';
import { estimate } from '../oop';
import { fmt } from '../lib/format';
import { shortNetwork } from '../lib/networkLabels';
import { EstimateLine } from './EstimateLine';

// Job 2 — the same procedure priced across every network we hold. Collapsed by
// default (issue #73): the app is plan-first, so a wall of 50+ other Anthem
// networks on every procedure page is noise on the default path — and the
// query behind it (/rates/by_network) runs ~10s on the full corpus, so
// deferring the fetch until someone actually asks also kills that latency hit
// on the common path. The headline finding, once opened, is usually "the HMO
// is cheaper and far more predictable than the PPO".
export function NetworkCompare({ data, loading, expanded, onExpand, selectedNetwork, onPickNetwork, plan, rbcsCategory }) {
  if (!expanded) {
    return (
      <button
        onClick={onExpand}
        className="mt-8 w-full flex items-center justify-between gap-3 bg-slate-900 border border-slate-800 rounded-3xl px-6 py-4 text-left hover:border-slate-700 transition-colors"
      >
        <span className="text-sm font-bold text-slate-300">Compare this rate across your other Anthem networks</span>
        <ChevronDown size={16} className="text-slate-500 shrink-0" />
      </button>
    );
  }
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Comparing plans…
      </div>
    );
  }
  const nets = data?.networks || [];
  if (nets.length < 2) return null;

  const cheapest = nets[0];
  const others = nets.slice(1);
  const maxMed = Math.max(...nets.map(n => n.median || 0));

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">Does your plan matter?</h2>
      <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
        {(() => {
          const dear = others[others.length - 1];
          const mult = cheapest.median > 0 ? (dear.median / cheapest.median) : 1;
          if (mult >= 1.25)
            return <>On <span className="text-white font-semibold">{shortNetwork(cheapest.network_name)}</span> this
              runs about {fmt(cheapest.median)} — roughly {mult.toFixed(1)}× less than {shortNetwork(dear.network_name)}.</>;
          return <>Priced similarly across plans (~{fmt(cheapest.median)}).</>;
        })()}
        {' '}
        {cheapest.spread != null && others.some(n => n.spread >= 1.8) && (
          <>It&rsquo;s also steadier — {shortNetwork(cheapest.network_name)} varies {cheapest.spread}× by provider vs.{' '}
          {Math.max(...others.map(n => n.spread || 0))}× on the PPO plans.</>
        )}
      </p>

      <div className="mt-5 space-y-2.5">
        {nets.map((n, i) => {
          const active = n.network_name === selectedNetwork;
          return (
            <button
              key={i}
              onClick={() => onPickNetwork?.(active ? '' : n.network_name)}
              className={`w-full text-left rounded-2xl border p-4 transition-colors ${
                active ? 'border-indigo-500/60 bg-indigo-500/[0.06]' : 'border-slate-800 bg-slate-950/40 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-bold text-slate-200">
                  {shortNetwork(n.network_name)}
                  {active && <span className="ml-2 text-[10px] font-black uppercase tracking-wide text-indigo-400">your filter</span>}
                </span>
                <span className="text-lg font-black text-white tabular-nums shrink-0">{fmt(n.median)}</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div className="h-full bg-indigo-500/70 rounded-full" style={{ width: `${maxMed ? (n.median / maxMed) * 100 : 0}%` }} />
              </div>
              <EstimateLine className="mt-1.5" est={estimate(n.median, plan, { rbcsCategory })} />
              <div className="mt-2 text-[11px] text-slate-500 flex items-center gap-x-3 flex-wrap">
                <span>typically {fmt(n.typical_low)}–{fmt(n.typical_high)}</span>
                {n.spread != null && (
                  <span className={n.spread >= 2 ? 'text-amber-500/80' : 'text-slate-600'}>
                    {n.spread <= 1.15 ? 'flat rate' : `${n.spread}× provider spread`}
                  </span>
                )}
                <span className="text-slate-600" title="Distinct NPIs contracted for this code in this network, across the provider groups below. Groups are often facility/TIN rollups.">
                  {n.n_providers != null
                    ? <>{n.n_providers.toLocaleString()} providers · {n.n_groups.toLocaleString()} groups</>
                    : <>{n.n_groups.toLocaleString()} groups</>}
                </span>
              </div>
            </button>
          );
        })}
      </div>
      <p className="text-slate-600 text-[11px] mt-4">
        All Anthem networks. Tap a plan to filter the rest of the page to it.
      </p>
    </div>
  );
}
