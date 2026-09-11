import { useState, useEffect } from 'react';
import { Layers, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { getProcedureCategories } from '../api';

// Browse-by-category — RBCS taxonomy present in the data. Clicking a row runs the
// procedure search for that category name (matches via code_labels.search_text).
export function CategoryBrowser({ onPick }) {
  const [cats, setCats] = useState([]);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    getProcedureCategories().then(r => setCats(r.data || [])).catch(() => {});
  }, []);

  if (cats.length === 0) return null;

  const byCategory = cats.reduce((acc, c) => {
    (acc[c.category] ||= []).push(c);
    return acc;
  }, {});
  const order = Object.entries(byCategory)
    .map(([k, v]) => [k, v, v.reduce((s, x) => s + (x.provider_groups || 0), 0)])
    .sort((a, b) => b[2] - a[2]);

  return (
    <div className="mb-10 border border-slate-800 rounded-2xl bg-slate-900/40 overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
      >
        <span className="flex items-center gap-2.5 text-sm font-bold text-slate-300">
          <Layers size={15} className="text-indigo-400" />
          Browse by category
        </span>
        <ChevronDown size={16} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="border-t border-slate-800"
          >
            <div className="p-3 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
              {order.map(([category, subs]) => (
                <div key={category} className="rounded-xl overflow-hidden bg-slate-950/40">
                  <button
                    onClick={() => setExpanded(e => (e === category ? null : category))}
                    className="w-full flex items-center justify-between px-3.5 py-2.5 text-left hover:bg-white/[0.03] transition-colors"
                  >
                    <span className="text-xs font-bold text-slate-200">{category}</span>
                    <span className="text-[10px] text-slate-600 tabular-nums">{subs.length}</span>
                  </button>
                  <AnimatePresence>
                    {expanded === category && (
                      <motion.div
                        initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }}
                        className="overflow-hidden"
                      >
                        {subs.map(s => (
                          <button
                            key={s.subcategory}
                            onClick={() => { onPick(s.subcategory); setOpen(false); }}
                            className="w-full flex items-center justify-between px-3.5 py-2 text-left hover:bg-indigo-500/10 transition-colors border-t border-slate-800/60"
                          >
                            <span className="text-[11px] text-slate-400">{s.subcategory}</span>
                            <span className="text-[10px] text-slate-600 tabular-nums shrink-0 ml-2">
                              {s.n_codes} codes
                            </span>
                          </button>
                        ))}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
