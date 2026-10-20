import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getImagingProviders, getServiceLines } from '../api';
import { fmt } from '../lib/format';
import { shortNetwork } from '../lib/networkLabels';
import { IMAGING_EXPLAIN, basisNote } from '../lib/explain';
import { ExplorerHeader } from '../components/ExplorerHeader';
import { LocationBar } from '../components/LocationBar';
import { PlanGate } from '../components/PlanGate';
import { TrustBar } from '../components/TrustBar';

const NETWORK_KEY = 'hh_network_v1';
const DEFAULT_RADIUS_MI = 25;

function savedNetwork() {
  try { return localStorage.getItem(NETWORK_KEY) || ''; } catch { return ''; }
}

function practiceLabel(r) {
  return r.practice_name || (r.ga_indiv_names || []).join(', ') || `Practice ${r.practice_id}`;
}

function groupMenu(menu) {
  const groups = [];
  for (const m of menu) {
    let g = groups.find(x => x.name === m.group);
    if (!g) groups.push((g = { name: m.group, items: [] }));
    g.items.push(m);
  }
  return groups;
}

function Parts({ row }) {
  if (row.basis !== 'split') return null;
  return (
    <p className="text-[11px] text-slate-500 mt-1">
      Equipment {fmt(row.technical.typical)} + reading {fmt(row.professional.typical)}
    </p>
  );
}

function ResultRow({ row }) {
  const part = row.global || row.professional || row.technical;
  const ranged = part && part.max > part.min;
  const hasDistance = row.distance_mi != null;
  return (
    <li className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h3 className="text-white font-bold tracking-tight">{practiceLabel(row)}</h3>
        <p className="text-xs text-slate-500 mt-1">
          {[row.city, hasDistance ? `${row.distance_mi.toFixed(1)} mi` : null,
            row.n_providers > 1 ? `${row.n_providers} clinicians` : null].filter(Boolean).join(' · ')}
        </p>
        <p className="text-[11px] text-slate-500 mt-2 max-w-md">{basisNote(row.basis)}</p>
      </div>
      <div className="text-right">
        <div className="text-2xl font-black text-white tracking-tight">{fmt(row.typical_rate)}</div>
        <div className="text-[11px] text-slate-500">typical negotiated price</div>
        {ranged && (
          <div className="text-[11px] text-slate-500 mt-0.5">{fmt(part.min)} to {fmt(part.max)} across contracts</div>
        )}
        <Parts row={row} />
      </div>
    </li>
  );
}

// Flow A for outpatient imaging: pick a scan, see what each in-network practice
// has negotiated. Standalone from the rate explorer — one job, one screen.
export function ImagingFinder() {
  const [network, setNetwork] = useState(savedNetwork);
  const [menu, setMenu] = useState([]);
  const [code, setCode] = useState('');
  const [zip, setZip] = useState('');
  const [radiusMi, setRadiusMi] = useState(DEFAULT_RADIUS_MI);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    try {
      if (network) localStorage.setItem(NETWORK_KEY, network);
      else localStorage.removeItem(NETWORK_KEY);
    } catch { /* storage disabled; the plan just won't persist */ }
  }, [network]);

  useEffect(() => {
    getServiceLines().then(r => setMenu(r.data?.imaging?.menu || [])).catch(() => setError('Could not load the scan list.'));
  }, []);

  const near = /^\d{5}$/.test(zip) ? { zip, radius_mi: radiusMi } : undefined;
  useEffect(() => {
    if (!network || !code) { setData(null); return; }
    let live = true;
    setLoading(true);
    setError('');
    getImagingProviders(code, network, near)
      .then(r => { if (live) setData(r.data); })
      .catch(() => { if (live) { setData(null); setError('Could not load prices. Check your ZIP code and try again.'); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [network, code, near?.zip, radiusMi]);

  const groups = useMemo(() => groupMenu(menu), [menu]);
  const scan = menu.find(m => m.code === code);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30 overflow-x-hidden">
      <ExplorerHeader lockedServiceLine="imaging" />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        <Link to="/" className="inline-block mb-6 text-xs text-slate-500 hover:text-slate-300 transition-colors">
          ← All tasks
        </Link>
        <TrustBar selectedNetwork={network} />

        {!network ? (
          <PlanGate
            selectedPlan={network}
            onSelect={setNetwork}
            heading="Which plan are you on?"
            body="Imaging prices are negotiated per plan. Pick yours and every price here is the one your plan's network has agreed."
          />
        ) : (
          <>
            <p className="mb-8 text-xs text-slate-500">
              On <span className="text-slate-300 font-semibold">{shortNetwork(network)}</span>.{' '}
              <button onClick={() => { setNetwork(''); setData(null); }} className="underline underline-offset-2 hover:text-slate-300">
                change plan
              </button>
            </p>

            <label className="block mb-6 text-xs text-slate-400">
              <span className="block mb-2 font-semibold uppercase tracking-widest text-[10px] text-slate-500">Which scan?</span>
              <select
                value={code} aria-label="Which scan?"
                onChange={e => setCode(e.target.value)}
                className="w-full sm:w-96 bg-slate-900 border border-slate-800 focus:border-indigo-500/60 rounded-lg px-3 h-10 text-sm text-slate-200 outline-none"
              >
                <option value="">Choose a scan</option>
                {groups.map(g => (
                  <optgroup key={g.name} label={g.name}>
                    {g.items.map(m => <option key={m.code} value={m.code}>{m.label}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>

            <LocationBar zip={zip} onZip={setZip} radiusMi={radiusMi} onRadius={setRadiusMi} />

            {error && <p role="alert" className="mb-6 text-sm text-amber-400">{error}</p>}
            {loading && <p className="text-sm text-slate-500">Loading prices…</p>}

            {data && !loading && (
              <section aria-label="Results">
                <h2 className="text-white font-black text-xl tracking-tight mb-1">{scan?.label}</h2>
                <p className="text-xs text-slate-500 mb-5">
                  {data.n_practices} in-network {data.n_practices === 1 ? 'practice' : 'practices'}, cheapest first and partial prices last
                </p>
                {data.results.length === 0
                  ? <p className="text-sm text-slate-400">No in-network practices have a price for this scan{near ? ' within that distance' : ''}.</p>
                  : <ul className="space-y-3">{data.results.map(r => <ResultRow key={r.practice_id} row={r} />)}</ul>}

                <div className="mt-10 rounded-2xl border border-slate-800 bg-slate-900/40 p-5 text-xs text-slate-400 leading-relaxed space-y-3">
                  <p><span className="text-slate-200 font-semibold">What this number is. </span>{IMAGING_EXPLAIN.number}</p>
                  <p><span className="text-slate-200 font-semibold">How you pay it. </span>{IMAGING_EXPLAIN.pay}</p>
                  <p><span className="text-slate-200 font-semibold">Where it comes from. </span>{IMAGING_EXPLAIN.source}</p>
                  <div>
                    <p className="text-slate-200 font-semibold mb-1">What it does not cover</p>
                    <ul className="list-disc pl-5 space-y-1">
                      {IMAGING_EXPLAIN.limits.map(l => <li key={l}>{l}</li>)}
                    </ul>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
