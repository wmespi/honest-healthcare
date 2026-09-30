import { Info, Activity } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { fmt } from '../lib/format';
import { CustomTooltip } from './CustomTooltip';

// The "how is this priced in general" view: the negotiated-rate disclaimer,
// the min/median/avg/max stat grid, the meta row, and the distribution
// histogram. Useful when browsing or comparing providers; noise once a
// specific provider + procedure is chosen (#73) — routes/Explorer.jsx swaps
// this for the compact RateSitsStrip inside the cost card in that case, so
// this component is only ever rendered for the other views.
export function RateOverview({ summary, specialty, selectedCode, buckets, medianBucket }) {
  return (
    <>
      {/* Negotiated-rate disclaimer */}
      <div className="mb-6 flex items-start gap-2.5 text-xs text-slate-500 bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-3">
        <Info size={14} className="shrink-0 mt-0.5 text-slate-600" />
        <span>
          These are <span className="text-slate-300 font-semibold">negotiated rates</span> — the price your plan and
          the provider agreed on, before your benefits apply. What you actually pay depends on your deductible,
          coinsurance, copay, and out-of-pocket max.
        </span>
      </div>

      {/* Summary stats. Across every procedure in a network (no code picked)
          min / max / avg are dominated by unrelated, sometimes six-figure
          codes (#51) -- show the typical middle of the distribution instead. */}
      {selectedCode?.code ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Min',    value: fmt(summary.min),    color: 'text-emerald-400' },
            { label: 'Median', value: fmt(summary.median), color: 'text-indigo-400'  },
            { label: 'Average',value: fmt(summary.avg),    color: 'text-violet-400'  },
            { label: 'Max',    value: summary.max_capped ? `${fmt(summary.max)}+` : fmt(summary.max), color: 'text-rose-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
              <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest mb-2">{label}</div>
              <div className={`text-2xl font-black ${color}`}>{value}</div>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-4 mb-3">
            {[
              { label: 'Lower typical', value: fmt(summary.p25),    color: 'text-emerald-400' },
              { label: 'Median',        value: fmt(summary.median), color: 'text-indigo-400'  },
              { label: 'Upper typical', value: fmt(summary.p75),    color: 'text-violet-400'  },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
                <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest mb-2">{label}</div>
                <div className={`text-2xl font-black ${color}`}>{value}</div>
              </div>
            ))}
          </div>
          <p className="mb-8 text-xs text-slate-500">
            The middle half of every rate line in this network, across all procedures
            &mdash; not the price of any one thing. Pick a procedure for a real price.
          </p>
        </>
      )}

      {specialty && selectedCode?.code && (
        <p className="mb-4 text-xs text-indigo-300/90">
          Scoped to provider groups that include a <span className="font-semibold">{specialty}</span> provider —
          the rate still belongs to the whole group.
        </p>
      )}

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-6 text-xs text-slate-500">
        {selectedCode?.code
          ? <><span className="font-mono font-black text-white text-sm">{selectedCode.code}</span>
              <span className="uppercase font-bold text-slate-600">{selectedCode.type}</span></>
          : <span className="font-black text-white text-sm">Network Overview</span>
        }
        {summary.n_providers != null && (
          <span title="Distinct NPIs across these provider groups. Groups are often facility/TIN rollups, so one contract can cover thousands of NPIs.">
            <span className="text-white font-black">{summary.n_providers.toLocaleString()}</span> providers
          </span>
        )}
        {summary.provider_groups != null
          ? <span><span className="text-white font-black">{summary.provider_groups.toLocaleString()}</span> provider groups</span>
          : summary.n_codes != null && (
              <span><span className="text-white font-black">{summary.n_codes.toLocaleString()}</span> procedures priced</span>
            )}
        <span><span className="text-white font-black">{summary.total_entries.toLocaleString()}</span> rate entries</span>
        {summary.min > 0 && !summary.max_capped && summary.max / summary.min >= 1.05 && (
          <span className="text-indigo-400 font-bold">{(summary.max / summary.min).toFixed(1)}× spread</span>
        )}
      </div>

      {/* Histogram */}
      <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-white font-black text-xl tracking-tight">Rate Distribution</h2>
            <p className="text-slate-500 text-xs mt-1">
              {selectedCode?.code ? 'Provider groups' : 'Negotiated rate lines'} per price range ·{' '}
              <span className="text-indigo-400">— median {fmt(summary.median)}</span>
            </p>
          </div>
          <Activity size={18} className="text-slate-600" />
        </div>
        <ResponsiveContainer width="100%" height={340}>
          <BarChart data={buckets} margin={{ top: 16, right: 8, left: 0, bottom: 48 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: '#475569', fontSize: 11, fontWeight: 700 }}
              angle={-35}
              textAnchor="end"
              interval={Math.max(0, Math.floor(buckets.length / 8) - 1)}
            />
            <YAxis
              tick={{ fill: '#475569', fontSize: 10, fontWeight: 700 }}
              width={36}
              allowDecimals={false}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(99,102,241,0.1)' }} />
            <Bar dataKey="provider_groups" fill="#6366f1" radius={[4, 4, 0, 0]} />
            {medianBucket && (
              <ReferenceLine
                x={medianBucket.label}
                stroke="#818cf8"
                strokeDasharray="4 3"
                strokeWidth={2}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
