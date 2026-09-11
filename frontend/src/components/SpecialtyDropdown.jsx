import { useState, useEffect, useRef } from 'react';
import { X, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { getSpecialties } from '../api';

// Specialty scope — a filter, like Setting/Network. Default "All specialties".
// Distinct from picking one Provider (that drills to a single NPI).
export function SpecialtyDropdown({ selected, onSelect, network }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState([]);
  const ref = useRef(null);

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => { getSpecialties(query, network || undefined).then(r => setOpts(r.data || [])).catch(() => {}); }, 200);
    return () => clearTimeout(t);
  }, [query, open, network]);

  return (
    <div ref={ref} className="relative flex items-center gap-2">
      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest shrink-0">Specialty</span>
      <button
        onClick={() => setOpen(o => !o)}
        className={`flex items-center gap-1.5 bg-slate-900 border rounded-xl px-3 h-8 max-w-[200px] transition-all ${open ? 'border-indigo-500/50' : selected ? 'border-indigo-500/50' : 'border-slate-800'}`}
      >
        <span className={`text-xs truncate ${selected ? 'text-indigo-300 font-bold' : 'text-slate-400'}`}>{selected || 'All specialties'}</span>
        {selected
          ? <X size={12} className="text-slate-500 hover:text-white shrink-0" onClick={(e) => { e.stopPropagation(); onSelect(''); }} />
          : <ChevronDown size={12} className={`text-slate-500 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
            className="absolute top-full left-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl overflow-hidden z-[999] shadow-2xl min-w-[260px] max-h-72 overflow-y-auto">
            <div className="p-2 border-b border-white/5">
              <input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. cardiology…"
                className="w-full bg-slate-800/80 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-slate-500 outline-none" />
            </div>
            <button onClick={() => { onSelect(''); setOpen(false); }}
              className={`w-full px-4 py-2.5 text-left text-sm border-b border-white/5 hover:bg-white/5 ${!selected ? 'text-indigo-400 font-bold' : 'text-slate-400'}`}>
              {!selected && '✓ '}All specialties
            </button>
            {opts.map((sp, i) => (
              <button key={i} onClick={() => { onSelect(sp.specialty); setOpen(false); }}
                className="w-full px-4 py-2.5 text-left hover:bg-white/5 transition-colors border-b border-white/5 last:border-0 flex items-center justify-between gap-3">
                <span className={`text-sm truncate ${selected === sp.specialty ? 'text-indigo-400 font-bold' : 'text-slate-200'}`}>{sp.specialty}</span>
                <span className="text-[10px] text-emerald-400 font-black shrink-0">{sp.n_with_rates.toLocaleString()}</span>
              </button>
            ))}
            {opts.length === 0 && <div className="px-4 py-3 text-[11px] text-slate-600">No specialties match.</div>}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
