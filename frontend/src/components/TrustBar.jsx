import { useState, useEffect } from 'react';
import { Info, X } from 'lucide-react';
import { getHealth } from '../api';

// Dataset coverage + freshness — so a partial dataset announces itself (issue #32).
export function TrustBar({ selectedNetwork }) {
  const [h, setH] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem('hh_trustbar_dismissed') === '1'; } catch { return false; }
  });
  useEffect(() => { getHealth().then(r => setH(r.data)).catch(() => {}); }, []);
  if (dismissed || !h || h.priceable_npis == null) return null;

  const asOf = h.as_of
    ? new Date(h.as_of + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : null;
  const nNet = (h.networks || []).length;
  const allNets = !selectedNetwork;

  return (
    <div className="mb-8 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-2.5 text-[11px] text-slate-500 flex items-start gap-2">
      <Info size={13} className="shrink-0 mt-0.5 text-slate-600" />
      <div className="flex-1 leading-relaxed">
        <span className="text-slate-400 font-semibold">{h.priceable_npis.toLocaleString()}</span> providers ·{' '}
        <span className="text-slate-400 font-semibold">{h.n_codes?.toLocaleString()}</span> billing codes ·{' '}
        {nNet} Anthem network{nNet === 1 ? '' : 's'}{asOf && <> · rates as of <span className="text-slate-400">{asOf}</span></>}
        {allNets && nNet > 1 && (
          <> — <span className="text-amber-400/80">“All Networks” mixes GA Blue Value with national mirror data;
          pick a plan for the Georgia individual-market rates.</span></>
        )}
      </div>
      <button
        onClick={() => { setDismissed(true); try { localStorage.setItem('hh_trustbar_dismissed', '1'); } catch { /* ignore */ } }}
        className="shrink-0 text-slate-600 hover:text-slate-300"
        aria-label="Dismiss"
      ><X size={12} /></button>
    </div>
  );
}
