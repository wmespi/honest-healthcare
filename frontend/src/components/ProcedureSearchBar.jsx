import { Search } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { fmt } from '../lib/format';
import { cleanProcedureName } from '../lib/procedureNames';
import { NetworkDropdown } from './NetworkDropdown';

// The general /explore search row: the plan picker + free-text procedure /
// billing-code search with its suggestions dropdown. Hidden on a locked
// service-line route (#83/#87) — that route already knows what it's shopping
// for, so the general catalog search is noise there (see routes/Explorer.jsx).
export function ProcedureSearchBar({
  selectedPlan, onPlanSelect,
  query, onQueryChange, onFocus, onBlur,
  showSuggestions, suggestions, onSuggestionClick,
}) {
  return (
    <div className="flex flex-col sm:flex-row gap-3 mb-10">
      <NetworkDropdown selectedPlan={selectedPlan} onSelect={onPlanSelect} />

      {/* Billing code / procedure search */}
      <div className="flex-1 relative bg-slate-900 border border-slate-800 rounded-2xl px-4 flex items-center focus-within:border-indigo-500/50 transition-all">
        <Search className="text-slate-500 shrink-0" size={18} />
        <input
          type="text"
          placeholder="Search procedure or billing code..."
          className="w-full bg-transparent h-14 pl-3 pr-4 outline-none text-white placeholder:text-slate-600 text-sm"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onFocus={onFocus}
          onBlur={onBlur}
        />

        <AnimatePresence>
          {showSuggestions && suggestions.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="absolute top-full left-0 right-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl overflow-hidden z-[999] shadow-2xl max-h-80 overflow-y-auto"
            >
              {suggestions.map((sug, i) => (
                <button
                  key={i}
                  onClick={() => onSuggestionClick(sug)}
                  className="w-full px-5 py-3.5 text-left hover:bg-white/5 transition-colors flex items-center justify-between gap-4 border-b border-white/5 last:border-0"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-white break-words">
                      {sug.label || cleanProcedureName(sug.name) || sug.billing_code}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[11px] font-mono text-indigo-400">{sug.billing_code}</span>
                      <span className="text-[10px] text-slate-600 uppercase tracking-wide">{sug.billing_code_type}</span>
                      {sug.rbcs_subcategory && sug.rbcs_subcategory !== sug.label && (
                        <span className="text-[10px] text-slate-600 truncate">· {sug.rbcs_subcategory}</span>
                      )}
                    </div>
                  </div>
                  <span className="text-xs text-slate-500 shrink-0 tabular-nums">
                    {sug.min_rate != null
                      ? (sug.min_rate === sug.max_rate ? fmt(sug.min_rate) : `${fmt(sug.min_rate)}–${fmt(sug.max_rate)}`)
                      : sug.provider_groups != null
                        ? `${sug.provider_groups.toLocaleString()} provider groups`
                        : null}
                  </span>
                </button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
