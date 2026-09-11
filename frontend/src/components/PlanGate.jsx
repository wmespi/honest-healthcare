import { ShieldCheck } from 'lucide-react';
import { motion } from 'framer-motion';
import { NetworkDropdown } from './NetworkDropdown';

// The plan-first front door (issue: Flow A). Negotiated rates are meaningless
// without a plan — every rate view is scoped to one network — so we ask for the
// plan before anything else. Reuses the curated picker; a link drops the gate
// for the "just show me the data" case (the trust bar already warns that
// "All Networks" mixes GA Blue Value with national mirror rows). `heading`/
// `body`/`browseLabel` let a locked service-line route (#87) contextualize the
// ask ("which plan, so we can price *these* providers") instead of the
// generic copy — defaults preserve the original wording on /explore.
export function PlanGate({ selectedPlan, onSelect, onBrowseAll, heading, body, browseLabel }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
      className="mt-4 mb-10 bg-slate-900 border border-slate-800 rounded-3xl p-8 sm:p-10 text-center"
    >
      <div className="w-12 h-12 mx-auto mb-5 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl flex items-center justify-center">
        <ShieldCheck size={22} className="text-indigo-400" />
      </div>
      <h2 className="text-white font-black text-2xl tracking-tight">{heading || 'Start with your plan'}</h2>
      <p className="text-slate-400 text-sm mt-2.5 max-w-sm mx-auto leading-relaxed">
        {body || <>Negotiated rates are plan-specific — a provider&rsquo;s price on an HMO isn&rsquo;t
        their price on a PPO. Pick your plan and everything on this page is scoped to it.</>}
      </p>
      <div className="mt-6 flex justify-center">
        <NetworkDropdown selectedPlan={selectedPlan} onSelect={onSelect} />
      </div>
      <button
        onClick={onBrowseAll}
        className="mt-6 text-xs text-slate-500 hover:text-slate-300 transition-colors underline underline-offset-2"
      >
        {browseLabel || 'Explore all networks without picking a plan'}
      </button>
    </motion.div>
  );
}
