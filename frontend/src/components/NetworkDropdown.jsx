import { useState, useEffect, useRef } from 'react';
import { Layers, X, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { getPlans, getNetworks } from '../api';

// Searchable network dropdown — the reliable per-plan filter. Fetches
// /networks server-side on open and debounces search.
export function NetworkDropdown({ selectedPlan, onSelect }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [plans, setPlans] = useState([]);       // raw network_name strings
  const [namedPlans, setNamedPlans] = useState([]); // curated { plan, carrier, network_name, available }
  const ref = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => { getPlans().then(r => setNamedPlans(r.data || [])).catch(() => {}); }, []);
  // If the selected network matches a curated plan, show its friendly name.
  const selectedLabel = namedPlans.find(p => p.network_name === selectedPlan)?.plan || selectedPlan;

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const loadNetworks = (q) =>
    getNetworks(q)
      .then(r => setPlans((r.data || []).map(n => n.network_name).filter(Boolean)))
      .catch(() => {});

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
    if (open && plans.length === 0) loadNetworks('');
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => loadNetworks(query), 250);
    return () => clearTimeout(t);
  }, [query, open]);

  const filtered = plans;

  const handleSelect = (plan) => {
    onSelect(plan);
    setOpen(false);
    setQuery('');
  };

  return (
    <div ref={ref} className="relative sm:w-72 shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        className={`w-full h-14 px-4 flex items-center gap-3 bg-slate-900 border rounded-2xl text-left transition-all ${open ? 'border-indigo-500/50' : 'border-slate-800'}`}
      >
        <Layers size={18} className="text-slate-500 shrink-0" />
        <span className="flex-1 text-sm truncate text-white">{selectedLabel || 'All Networks'}</span>
        {selectedPlan ? (
          <button
            onClick={(e) => { e.stopPropagation(); handleSelect(''); }}
            className="text-slate-500 hover:text-white p-0.5 shrink-0"
          >
            <X size={14} />
          </button>
        ) : (
          <ChevronDown size={14} className={`text-slate-500 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className="absolute top-full left-0 right-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl z-[999] shadow-2xl overflow-hidden"
          >
            <div className="p-2 border-b border-white/5">
              <input
                ref={inputRef}
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Your plan, or search networks..."
                className="w-full bg-slate-800/80 rounded-xl px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none"
              />
            </div>
            <div className="max-h-60 overflow-y-auto">
              <button
                onClick={() => handleSelect('')}
                className={`w-full px-4 py-3 text-left text-sm flex items-center gap-2 hover:bg-white/5 transition-colors border-b border-white/5 ${!selectedPlan ? 'text-indigo-400' : 'text-slate-400'}`}
              >
                {!selectedPlan && <span>✓</span>}
                <span className={!selectedPlan ? 'font-bold' : ''}>All Networks</span>
              </button>

              {namedPlans.filter(p => p.available && (!query ||
                  p.plan.toLowerCase().includes(query.toLowerCase()) ||
                  (p.carrier || '').toLowerCase().includes(query.toLowerCase()))).length > 0 && (
                <>
                  <div className="px-4 pt-2.5 pb-1 text-[9px] font-black uppercase tracking-widest text-slate-600">Your plan</div>
                  {namedPlans
                    .filter(p => p.available && (!query ||
                      p.plan.toLowerCase().includes(query.toLowerCase()) ||
                      (p.carrier || '').toLowerCase().includes(query.toLowerCase())))
                    .map((p, i) => (
                      <button key={`np${i}`} onClick={() => handleSelect(p.network_name)}
                        className={`w-full px-4 py-2.5 text-left text-sm flex items-start gap-2 hover:bg-white/5 transition-colors ${selectedPlan === p.network_name ? 'text-indigo-400' : 'text-slate-200'}`}>
                        <span className="shrink-0 mt-0.5">{selectedPlan === p.network_name ? '✓' : ' '}</span>
                        <span>
                          <span className={selectedPlan === p.network_name ? 'font-bold' : 'font-medium'}>{p.plan}</span>
                          <span className="block text-[10px] text-slate-500">{[p.carrier, p.market].filter(Boolean).join(' · ')}</span>
                        </span>
                      </button>
                    ))}
                  <div className="px-4 pt-2.5 pb-1 text-[9px] font-black uppercase tracking-widest text-slate-600 border-t border-white/5">Or a network directly</div>
                </>
              )}

              {filtered.length === 0 && (
                <div className="px-4 py-3 text-slate-500 text-sm italic">No networks match</div>
              )}
              {filtered.map((p, i) => (
                <button
                  key={i}
                  onClick={() => handleSelect(p)}
                  className={`w-full px-4 py-3 text-left text-sm flex items-start gap-2 hover:bg-white/5 transition-colors ${selectedPlan === p ? 'text-indigo-400' : 'text-slate-300'}`}
                >
                  <span className="shrink-0 mt-0.5">{selectedPlan === p ? '✓' : ' '}</span>
                  <span className={`break-words ${selectedPlan === p ? 'font-bold' : ''}`}>{p}</span>
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
