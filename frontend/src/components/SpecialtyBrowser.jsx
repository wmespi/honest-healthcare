import { useState, useEffect } from 'react';
import { getSpecialties } from '../api';

// "Pick your care" — the specialty step, between plan and providers. An
// always-open searchable list, alphabetical (the endpoint's order), with the
// provider count shown as context, not a sort key.
export function SpecialtyBrowser({ onSelect, network }) {
  const [query, setQuery] = useState('');
  const [opts, setOpts] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      getSpecialties(query, network || undefined)
        .then(r => { if (!cancelled) setOpts(r.data || []); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoaded(true); });
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [query, network]);

  return (
    <div className="mb-10">
      <h2 className="text-white font-black text-xl tracking-tight">What kind of care do you need?</h2>
      <p className="text-slate-500 text-xs mt-1">
        Pick a specialty to see its providers and their rates on your plan — or search a procedure above.
      </p>
      <div className="mt-4 bg-slate-900 border border-slate-800 rounded-3xl overflow-hidden">
        <div className="p-3 border-b border-slate-800">
          <input
            value={query} onChange={e => setQuery(e.target.value)}
            placeholder="e.g. cardiology, dermatology, physical therapy…"
            className="w-full bg-slate-800/80 rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-slate-500 outline-none"
          />
        </div>
        <div className="max-h-96 overflow-y-auto divide-y divide-slate-800/60">
          {!loaded && opts.length === 0 && (
            <div className="px-5 py-4 text-[10px] text-slate-600 uppercase tracking-widest font-black animate-pulse">
              Loading specialties…
            </div>
          )}
          {opts.map((sp, i) => (
            <button
              key={i} onClick={() => onSelect(sp.specialty)}
              className="w-full px-5 py-3 text-left hover:bg-indigo-500/[0.06] transition-colors flex items-center justify-between gap-4"
            >
              <span className="text-sm text-white font-medium truncate">{sp.specialty}</span>
              <span className="text-[11px] text-slate-500 shrink-0 tabular-nums">
                {sp.n_with_rates.toLocaleString()} provider{sp.n_with_rates === 1 ? '' : 's'}
              </span>
            </button>
          ))}
          {loaded && opts.length === 0 && (
            <div className="px-5 py-4 text-xs text-slate-600">No specialties match “{query}”.</div>
          )}
        </div>
      </div>
    </div>
  );
}
