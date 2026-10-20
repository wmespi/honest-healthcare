import { ShieldCheck, TrendingUp } from 'lucide-react';
import { motion } from 'framer-motion';

// The Explorer route's nav bar + hero — the copy changes when the route locks
// a service line (#83/#87, e.g. /find-care) vs. the general /explore flow.
export function ExplorerHeader({ lockedServiceLine }) {
  return (
    <>
      <nav className="border-b border-white/5 bg-slate-950/80 backdrop-blur-3xl sticky top-0 z-[100]">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 h-16 sm:h-20 flex items-center justify-between gap-3">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 bg-gradient-to-tr from-indigo-700 to-indigo-500 rounded-xl flex items-center justify-center text-white shadow-2xl shadow-indigo-500/20 border border-white/10">
              <ShieldCheck size={24} strokeWidth={3} />
            </div>
            <div className="flex flex-col">
              <span className="text-xl font-black tracking-tighter text-white">HONEST HEALTHCARE</span>
              <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">Anthem Rate Explorer</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <TrendingUp size={16} className="text-indigo-400 shrink-0" />
            <span className="text-xs text-slate-400 font-medium hidden sm:inline">Georgia Blue Value HMO · MRF Data</span>
            <span className="text-xs text-slate-400 font-medium sm:hidden">MRF</span>
          </div>
        </div>
      </nav>

      <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="mb-10 sm:mb-12">
        {lockedServiceLine === 'pcp' ? (
          <>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white mb-4 tracking-tighter leading-[0.95] sm:leading-[0.9]">
              Find your <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-violet-500">primary care doctor</span>
            </h1>
            <p className="text-slate-400 text-base sm:text-lg max-w-xl leading-relaxed">
              Compare in-network primary care providers on cost — negotiated rates
              for a new-patient visit, before your benefits apply.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white mb-4 tracking-tighter leading-[0.95] sm:leading-[0.9]">
              What does <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-violet-500">your plan</span>
              <br />actually pay?
            </h1>
            <p className="text-slate-400 text-base sm:text-lg max-w-xl leading-relaxed">
              Choose your plan, pick the kind of care you need, and see what each provider
              has negotiated — before your benefits apply.
            </p>
          </>
        )}
      </motion.div>
    </>
  );
}
