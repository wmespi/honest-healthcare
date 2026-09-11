import { useState } from 'react';
import { ShieldCheck, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { COPAY_BUCKETS, COPAY_LABELS } from '../oop';

// "Your cost sharing" — deductible/coinsurance/copay inputs, persisted to
// localStorage (issue #30). Distinct from the plan *identity* picker in the
// network dropdown (issue #33).
export function PlanPanel({ form, setField, setCopay, clear, configured }) {
  const [open, setOpen] = useState(false);
  const [copaysOpen, setCopaysOpen] = useState(false);
  const field = (k, label, ph) => (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wide">{label}</span>
      <div className="flex items-center bg-slate-950 border border-slate-800 rounded-lg px-2.5 h-9">
        <span className="text-slate-600 text-xs">$</span>
        <input
          type="number" inputMode="decimal" min="0" placeholder={ph}
          value={form[k]} onChange={(e) => setField(k, e.target.value)}
          className="w-full bg-transparent outline-none text-sm text-white pl-1 placeholder:text-slate-700"
        />
      </div>
    </label>
  );

  return (
    <div className="mb-6 border border-slate-800 rounded-2xl bg-slate-900/40 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
      >
        <span className="flex items-center gap-2.5 text-sm font-bold text-slate-300">
          <ShieldCheck size={15} className="text-indigo-400" />
          Your cost sharing
          {configured
            ? <span className="text-[10px] font-black uppercase tracking-wide text-emerald-400">estimating</span>
            : <span className="text-[10px] text-slate-600 font-normal">add your deductible / copay to estimate what you'd pay</span>}
        </span>
        <ChevronDown size={15} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="px-5 pb-5 pt-1 space-y-4 border-t border-slate-800">
              <div className="grid grid-cols-2 gap-3">
                {field('deductibleTotal', 'Deductible', '2,000')}
                {field('deductibleMet', 'Deductible met', '0')}
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wide">Coinsurance</span>
                  <div className="flex items-center bg-slate-950 border border-slate-800 rounded-lg px-2.5 h-9">
                    <input
                      type="number" inputMode="decimal" min="0" max="100" placeholder="20"
                      value={form.coinsurance} onChange={(e) => setField('coinsurance', e.target.value)}
                      className="w-full bg-transparent outline-none text-sm text-white placeholder:text-slate-700"
                    />
                    <span className="text-slate-600 text-xs">%</span>
                  </div>
                </label>
                <div />
                {field('oopMax', 'Out-of-pocket max', '8,000')}
                {field('oopMet', 'Out-of-pocket met', '0')}
              </div>

              <div>
                <button onClick={() => setCopaysOpen((o) => !o)} className="text-[11px] font-bold text-slate-500 hover:text-slate-300 flex items-center gap-1">
                  Flat copays (optional) <ChevronDown size={12} className={copaysOpen ? 'rotate-180' : ''} />
                </button>
                {copaysOpen && (
                  <div className="grid grid-cols-2 gap-3 mt-2">
                    {COPAY_BUCKETS.map((b) => (
                      <label key={b} className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wide">{COPAY_LABELS[b]}</span>
                        <div className="flex items-center bg-slate-950 border border-slate-800 rounded-lg px-2.5 h-9">
                          <span className="text-slate-600 text-xs">$</span>
                          <input
                            type="number" inputMode="decimal" min="0" placeholder="—"
                            value={form.copays[b] ?? ''} onChange={(e) => setCopay(b, e.target.value)}
                            className="w-full bg-transparent outline-none text-sm text-white pl-1 placeholder:text-slate-700"
                          />
                        </div>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between pt-1">
                <p className="text-[11px] text-slate-600 leading-relaxed max-w-md">
                  Estimate only — real claims apply bundling, prior auth, out-of-network rules, and
                  separate facility fees we don't model.
                </p>
                {configured && (
                  <button onClick={clear} className="text-[11px] text-slate-500 hover:text-white shrink-0 ml-3">Clear</button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
