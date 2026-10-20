import { Link } from 'react-router-dom';
import { ShieldCheck, ChevronDown } from 'lucide-react';
import { motion } from 'framer-motion';

// The task-first landing (#87) — lead with the question, not the plan. One
// real card today ("we just support one menu item" — the owner's own framing
// for keeping this minimal); more service lines mean more cards here later,
// not a redesign of this page. The general explorer stays reachable, just
// demoted from front door to a quiet secondary link.
export function Landing() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30 flex flex-col">
      <nav className="border-b border-white/5 bg-slate-950/80 backdrop-blur-3xl">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 h-16 sm:h-20 flex items-center gap-4">
          <div className="w-10 h-10 bg-gradient-to-tr from-indigo-700 to-indigo-500 rounded-xl flex items-center justify-center text-white shadow-2xl shadow-indigo-500/20 border border-white/10">
            <ShieldCheck size={24} strokeWidth={3} />
          </div>
          <div className="flex flex-col">
            <span className="text-xl font-black tracking-tighter text-white">HONEST HEALTHCARE</span>
            <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">Anthem Rate Explorer</span>
          </div>
        </div>
      </nav>

      <main className="flex-1 max-w-3xl mx-auto w-full px-4 sm:px-6 py-16 sm:py-24 flex flex-col justify-center">
        <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} className="mb-10">
          <h1 className="text-4xl sm:text-5xl font-black text-white mb-4 tracking-tighter leading-[0.95]">
            What are you trying to do?
          </h1>
          <p className="text-slate-400 text-base sm:text-lg max-w-lg leading-relaxed">
            Pick the thing you need — we&rsquo;ll ask for your plan when it actually
            changes the price, not before.
          </p>
        </motion.div>

        <Link
          to="/find-care"
          className="group flex items-center justify-between gap-4 bg-slate-900 border border-slate-800 hover:border-indigo-500/50 rounded-3xl p-6 sm:p-8 transition-colors"
        >
          <div>
            <h2 className="text-white font-black text-xl tracking-tight">Find a primary care doctor</h2>
            <p className="text-slate-500 text-sm mt-1.5 max-w-md">
              Compare cost and identity across in-network PCPs for a new-patient visit.
            </p>
          </div>
          <ChevronDown size={20} className="text-slate-600 group-hover:text-indigo-400 shrink-0 -rotate-90 transition-colors" />
        </Link>

        <Link
          to="/explore"
          className="mt-8 self-start text-xs text-slate-500 hover:text-slate-300 transition-colors underline underline-offset-2"
        >
          Or explore all networks and procedures directly
        </Link>
      </main>
    </div>
  );
}
