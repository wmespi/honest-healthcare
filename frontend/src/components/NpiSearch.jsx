import { useState, useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { searchProviders } from '../api';
import { shortNetwork } from '../lib/networkLabels';
import { ProviderRow } from './ProviderRow';

// Provider filter — one specific NPI by name / number. A global lookup: it
// ignores the specialty scope (you're naming a specific person), and picking one
// clears the specialty so the two can't contradict. `label` is the parent's
// name for the current selection (so a pick made elsewhere still shows a name).
export function NpiSearch({ selectedNpi, onSelect, network, label }) {
  const [query, setQuery] = useState('');
  const [selectedLabel, setSelectedLabel] = useState('');
  const [providers, setProviders] = useState([]);
  const [open, setOpen] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => {
    if (!isFocused || query.length === 0) { setProviders([]); return; }
    const t = setTimeout(() => {
      searchProviders(query, undefined, undefined, network || undefined)
        .then(r => { setProviders(r.data); setOpen(true); }).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [query, isFocused, network]);

  const pickProvider = (s) => {
    const label = s.name || String(s.npi);
    onSelect(String(s.npi), label);
    setSelectedLabel(label);
    setQuery(''); setOpen(false);
  };
  const clear = () => { onSelect('', ''); setQuery(''); setSelectedLabel(''); };
  const firstNoRates = providers.findIndex(s => !s.has_rates);
  const planLabel = network ? shortNetwork(network) : '';

  return (
    <div ref={ref} className="flex items-center gap-2">
      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest shrink-0">Provider</span>
      {selectedNpi ? (
        <div className="flex items-center gap-2 bg-slate-900 border border-indigo-500/50 rounded-xl px-3 h-8 max-w-[220px]">
          <span className="text-xs text-indigo-300 font-bold truncate">{label || selectedLabel || selectedNpi}</span>
          <button onClick={clear} className="text-slate-500 hover:text-white transition-colors shrink-0"><X size={12} /></button>
        </div>
      ) : (
        <div className="relative">
          <div className={`flex items-center bg-slate-900 border rounded-xl px-3 h-8 gap-2 transition-all ${open ? 'border-indigo-500/50' : 'border-slate-800'}`}>
            <input
              type="text"
              placeholder="name or NPI…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => { setIsFocused(true); setOpen(true); }}
              onBlur={() => setTimeout(() => { setIsFocused(false); setOpen(false); }, 200)}
              className="bg-transparent outline-none text-xs text-white placeholder:text-slate-600 w-40 min-w-0"
            />
          </div>
          <AnimatePresence>
            {open && providers.length > 0 && (
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                className="absolute top-full left-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl overflow-hidden z-[999] shadow-2xl min-w-[280px] max-h-72 overflow-y-auto">
                {providers.map((s, i) => (
                  <div key={i}>
                    {i === firstNoRates && i > 0 && (
                      <div className="px-4 py-1 text-[9px] font-black uppercase tracking-widest text-slate-600 bg-white/[0.02] border-y border-white/5">
                        {planLabel ? `Not in ${planLabel}` : 'No rate data'} — {providers.length - firstNoRates} more
                      </div>
                    )}
                    <ProviderRow
                      s={s}
                      onPick={pickProvider}
                      disabled={!s.has_rates && !!network}
                      planLabel={planLabel}
                    />
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
