import { useState, useEffect, useCallback, useRef } from 'react';
import { getNetworks, searchBillingCodes, getRateDistribution, getRatesByProvider, getRateQuote, getProviderMenu, searchProviders, getProcedureCategories } from './api';
import { Search, ShieldCheck, Activity, Layers, TrendingUp, X, ChevronDown, Info, Building2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';

const fmt = (n) =>
  n == null ? '-' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Expands CMS/AMA abbreviations and title-cases procedure names for layman readability.
const ABBR = {
  OUTPT: 'Outpatient', INPT: 'Inpatient', PATIEN: 'Patient',
  ESTAB: 'Established', OFC: 'Office', HOSP: 'Hospital', SURG: 'Surgery',
  PROC: 'Procedure', EMER: 'Emergency', PREV: 'Preventive',
  SUBSEQ: 'Subsequent', INIT: 'Initial', MGT: 'Management',
  SVC: 'Service', SVCS: 'Services', ADMISS: 'Admission',
  PSYCH: 'Psychiatric', BEHAV: 'Behavioral', 'W/': 'with', 'W/O': 'without',
  NEC: 'Not Elsewhere Classified', NOS: 'Not Otherwise Specified',
  DX: 'Diagnosis', TX: 'Treatment', HX: 'History',
};
function cleanProcedureName(name) {
  if (!name) return name;
  return name.split(';').map(part =>
    part.trim().split(/\s+/).map(word => {
      const up = word.toUpperCase();
      if (ABBR[up]) return ABBR[up];
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    }).join(' ')
  ).join(' — ');
}

// Computes a clean step size so the x-axis reads $0, $50, $100... or $0, $5, $10...
function niceStep(maxVal) {
  if (maxVal <= 0) return 1;
  const rough = maxVal / 12;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  if (norm <= 1) return mag;
  if (norm <= 2) return 2 * mag;
  if (norm <= 5) return 5 * mag;
  return 10 * mag;
}

// Always starts at $0, uses clean intervals, includes empty buckets so the axis is continuous.
function bucketDistribution(distribution) {
  if (!distribution || distribution.length === 0) return [];
  const rates = distribution.map(d => d.rate);
  const dataMax = Math.max(...rates);
  const step = niceStep(dataMax);
  const numBuckets = Math.floor(dataMax / step) + 1;

  const buckets = Array.from({ length: numBuckets }, (_, i) => ({
    low: i * step,
    high: (i + 1) * step,
    provider_groups: 0,
  }));

  for (const d of distribution) {
    const idx = Math.min(Math.floor(d.rate / step), numBuckets - 1);
    if (idx >= 0) buckets[idx].provider_groups += d.provider_groups;
  }

  return buckets.map(b => ({
    label: `$${Math.round(b.low)}`,
    rate_mid: (b.low + b.high) / 2,
    provider_groups: b.provider_groups,
  }));
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-2xl">
      <p className="text-slate-400 text-xs font-bold mb-1">{label}</p>
      <p className="text-white font-black text-lg">
        {payload[0].value}{' '}
        <span className="text-slate-400 text-xs font-normal">provider groups</span>
      </p>
    </div>
  );
};

// Searchable network dropdown — the reliable per-plan filter. Fetches
// /networks server-side on open and debounces search.
function NetworkDropdown({ selectedPlan, onSelect }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [plans, setPlans] = useState([]);
  const ref = useRef(null);
  const inputRef = useRef(null);

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
        <span className="flex-1 text-sm truncate text-white">{selectedPlan || 'All Networks'}</span>
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
                placeholder="Search networks..."
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

function NpiSearch({ selectedNpi, onSelect }) {
  const [query, setQuery] = useState('');
  const [selectedLabel, setSelectedLabel] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setShowSuggestions(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (!isFocused || query.length === 0) { setSuggestions([]); setShowSuggestions(false); return; }
    const timer = setTimeout(() => {
      searchProviders(query)
        .then(res => { setSuggestions(res.data); setShowSuggestions(true); })
        .catch(() => {});
    }, 200);
    return () => clearTimeout(timer);
  }, [query, isFocused]);

  const handleSelect = (s) => {
    const label = s.name || String(s.npi);
    onSelect(String(s.npi), label);
    setSelectedLabel(label);
    setQuery('');
    setShowSuggestions(false);
  };

  const handleClear = () => { onSelect('', ''); setQuery(''); setSelectedLabel(''); };

  return (
    <div ref={ref} className="flex items-center gap-2">
      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest shrink-0">Provider</span>
      {selectedNpi ? (
        <div className="flex items-center gap-2 bg-slate-900 border border-indigo-500/50 rounded-xl px-3 h-8 max-w-[220px]">
          <span className="text-xs text-indigo-300 font-bold truncate">{selectedLabel || selectedNpi}</span>
          <button onClick={handleClear} className="text-slate-500 hover:text-white transition-colors shrink-0"><X size={12} /></button>
        </div>
      ) : (
        <div className="relative">
          <div className={`flex items-center bg-slate-900 border rounded-xl px-3 h-8 gap-2 transition-all ${showSuggestions ? 'border-indigo-500/50' : 'border-slate-800'}`}>
            <input
              type="text"
              placeholder="Search provider or NPI…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setTimeout(() => { setIsFocused(false); setShowSuggestions(false); }, 200)}
              className="bg-transparent outline-none text-xs text-white placeholder:text-slate-600 w-44"
            />
          </div>
          <AnimatePresence>
            {showSuggestions && suggestions.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                className="absolute top-full left-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl overflow-hidden z-[999] shadow-2xl min-w-[280px] max-h-72 overflow-y-auto"
              >
                {suggestions.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => handleSelect(s)}
                    className="w-full px-4 py-2.5 text-left hover:bg-white/5 transition-colors border-b border-white/5 last:border-0"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-white truncate">{s.name || s.npi}</span>
                      {s.has_rates && <span className="text-[9px] font-black uppercase tracking-wide text-emerald-400 shrink-0">has rates</span>}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5 truncate">
                      {[s.city, s.taxonomy_group, `NPI ${s.npi}`].filter(Boolean).join(' · ')}
                    </div>
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

// Browse-by-category — RBCS taxonomy present in the data. Clicking a row runs the
// procedure search for that category name (matches via code_labels.search_text).
function CategoryBrowser({ onPick }) {
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

// Light title-casing for the ALL-CAPS NPPES org names, keeping entity suffixes upper.
const ORG_SUFFIX = new Set(['LLC', 'INC', 'PC', 'PA', 'LLP', 'LP', 'MD', 'DO', 'DDS', 'CORP', 'CO']);
function titleCaseOrg(name) {
  if (!name) return name;
  return name.split(/\s+/).map(w => {
    const bare = w.replace(/[.,]/g, '').toUpperCase();
    if (ORG_SUFFIX.has(bare)) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  }).join(' ');
}

// Name for one contracted provider group. Small groups get their real practice
// name (NPPES org, else physician names); the big TIN/IPA rollups can't be
// named and say so plainly.
function providerLabel(r) {
  if (r.is_rollup) return 'Statewide contract group';
  const named = (r.named_practices || []).filter(Boolean);
  if (named.length) return titleCaseOrg(named[0]);
  const tax = (r.ga_taxonomies || []).find(t => t && t !== 'Other');
  if (tax) return tax;
  return `Provider group #${r.provider_group_id}`;
}

// "Does the provider matter?" — Job 3. Blue Value is close to a network-wide fee
// schedule, so for most codes every provider negotiated the same rate and the
// honest answer is "provider choice doesn't change the price". When rates do
// vary, show the ranked list with the named practices surfaced.
function ProviderRateTable({ data, loading }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Comparing providers…
      </div>
    );
  }
  const rows = data?.results || [];
  if (!rows.length) return null;
  const s = data.summary || {};

  const medians = rows.map(r => r.median_rate).filter(x => x != null);
  const lo = Math.min(...medians), hi = Math.max(...medians);
  const uniform = medians.length > 1 && hi - lo < Math.max(0.02 * lo, 1);
  const rangeStr = s.min === s.max ? fmt(s.min) : `${fmt(s.min)}–${fmt(s.max)}`;

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">Does the provider matter?</h2>

      {uniform ? (
        <div className="mt-4">
          <div className="text-3xl sm:text-4xl font-black text-white tracking-tight">{rangeStr}</div>
          <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
            Every in-network provider negotiated <span className="text-white font-semibold">the same rate</span> for
            this procedure.{' '}
            {s.min !== s.max && 'The spread is office vs. facility setting — not one provider vs. another. '}
            Picking a cheaper clinic won’t lower this price.
          </p>
          <p className="text-slate-600 text-xs mt-3">
            {s.n_groups} contracted provider groups · {(s.n_providers ?? 0).toLocaleString()} providers
          </p>
        </div>
      ) : (() => {
        // "Typical" = a contract carrying the full standard schedule (same floor
        // and ceiling as the network). Outliers miss the cheap tier or are
        // capped differently — those are the only actionable rows.
        const isTypical = r => r.min_rate === s.min && r.max_rate === s.max;
        const atModal = rows.filter(isTypical);
        const outliers = rows.filter(r => !isTypical(r));
        const Row = ({ r }) => (
          <div className="flex items-center justify-between gap-4 py-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Building2 size={13} className="text-slate-600 shrink-0" />
                <span className="text-sm text-white font-medium truncate">{providerLabel(r)}</span>
              </div>
              <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-x-2.5 flex-wrap">
                <span className="tabular-nums">{(r.npi_count || 0).toLocaleString()} providers</span>
                {r.ga_hospital_npis > 0 && <span className="text-amber-400">hospital-affiliated</span>}
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="text-lg font-black text-white tabular-nums">
                {r.min_rate === r.max_rate ? fmt(r.min_rate) : `${fmt(r.min_rate)}–${fmt(r.max_rate)}`}
              </div>
              {r.min_rate !== r.max_rate && <div className="text-[10px] text-slate-600">by setting</div>}
            </div>
          </div>
        );
        return (
          <>
            <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
              At most in-network providers this is <span className="text-white font-semibold">{rangeStr}</span>
              {' '}(depending on setting).
              {outliers.length > 0
                ? <> {outliers.length} contract{outliers.length > 1 ? 's differ' : ' differs'}:</>
                : <> Every contract we hold uses that same schedule.</>}
            </p>
            {outliers.length > 0 && (
              <div className="mt-4 divide-y divide-slate-800/60">
                {outliers.map((r, i) => <Row key={i} r={r} />)}
              </div>
            )}
            {atModal.length > 0 && (
              <p className="text-slate-600 text-[11px] mt-4">
                {atModal.length} contract{atModal.length > 1 ? 's' : ''} on the standard {rangeStr} schedule
                {atModal.some(r => !r.is_rollup) && (
                  <> — incl. {atModal.filter(r => !r.is_rollup).slice(0, 3).map(providerLabel).join(', ')}</>
                )}
              </p>
            )}
          </>
        );
      })()}
    </div>
  );
}

// Job 1 — the cost answer for one procedure at one provider. Shows a headline
// rate (a range when it varies by setting) and the breakdown by component
// (full procedure / professional fee / technical fee) and place of service.
function ProviderCostCard({ data, loading, providerName }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Pricing this procedure…
      </div>
    );
  }
  if (!data?.headline) return null;
  const { headline, components, is_component_split } = data;
  const range = (lo, hi) => (lo === hi ? fmt(lo) : `${fmt(lo)}–${fmt(hi)}`);

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">
        Negotiated cost{providerName ? <> at <span className="text-indigo-300">{providerName}</span></> : ''}
      </h2>

      <div className="mt-4 mb-2">
        <div className="text-4xl sm:text-5xl font-black text-white tracking-tight">
          {range(headline.rate, headline.max_rate)}
        </div>
        <div className="text-xs text-slate-500 mt-2">
          {headline.basis === 'global'
            ? (headline.pos_label
                ? <>Full procedure · {headline.pos_label}</>
                : <>Full procedure — varies by where it’s performed</>)
            : <>This code is billed only as separate parts — see the breakdown below</>}
        </div>
      </div>

      <div className="mt-6 space-y-3">
        {components.map(c => (
          <div key={c.modifier || 'global'} className="rounded-2xl bg-slate-950/50 border border-slate-800 p-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-bold text-slate-200">{c.label}</span>
              {c.modifier && <span className="text-[10px] font-mono text-slate-600 shrink-0">mod {c.modifier}</span>}
            </div>
            {c.description && <p className="text-[11px] text-slate-500 mt-1">{c.description}</p>}
            <div className="mt-2.5 divide-y divide-slate-800/50">
              {c.settings.map((s, i) => (
                <div key={i} className="flex items-center justify-between py-2 text-sm">
                  <span className="text-slate-400">{s.pos_label}</span>
                  <span className="text-white font-bold tabular-nums">{range(s.min_rate, s.max_rate)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {is_component_split && (
        <p className="text-[11px] text-slate-500 mt-4 leading-relaxed">
          Often billed as two line items — a <span className="text-slate-400">professional fee</span> (the physician’s
          reading) and a <span className="text-slate-400">technical fee</span> (the equipment and facility) — which
          together roughly equal the full rate. Which you’re charged depends on where it’s done and who interprets it.
        </p>
      )}
    </div>
  );
}

// The provider "menu" — every procedure the selected provider has a negotiated
// rate for, grouped by RBCS category. Shown when a provider is picked but no
// specific procedure. Clicking a row drills into that procedure.
function ProviderMenu({ data, loading, onPick, providerName, network, onClearNetwork }) {
  const [expanded, setExpanded] = useState(null);

  if (loading) {
    return (
      <div className="mt-2 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Loading this provider’s procedures…
      </div>
    );
  }
  const rows = data?.results || [];

  if (!rows.length) {
    const who = providerName || 'This provider';
    return (
      <div className="mt-2 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center">
        <p className="text-white font-bold">
          No negotiated rates for {who}{network ? <> in <span className="text-slate-300">{network}</span></> : ''}.
        </p>
        <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
          {network
            ? 'This provider isn’t in that network, or has no published rates there. Try a different network.'
            : 'We don’t hold any published rates for this provider yet.'}
        </p>
        {network && onClearNetwork && (
          <button
            onClick={onClearNetwork}
            className="mt-4 px-4 py-2 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition-colors"
          >
            Search all networks
          </button>
        )}
      </div>
    );
  }

  const byCat = rows.reduce((acc, r) => {
    (acc[r.rbcs_category || 'Other'] ||= []).push(r);
    return acc;
  }, {});
  const cats = Object.entries(byCat).sort((a, b) => b[1].length - a[1].length);

  return (
    <div className="mt-2">
      <div className="mb-4">
        <h2 className="text-white font-black text-xl tracking-tight">Procedure menu</h2>
        <p className="text-slate-500 text-xs mt-1">
          {rows.length.toLocaleString()} procedures this provider has a negotiated rate for. Tap one for the full breakdown.
        </p>
      </div>
      <div className="space-y-1.5">
        {cats.map(([cat, items]) => (
          <div key={cat} className="rounded-2xl overflow-hidden bg-slate-900 border border-slate-800">
            <button
              onClick={() => setExpanded(e => (e === cat ? null : cat))}
              className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
            >
              <span className="text-sm font-bold text-slate-200">{cat}</span>
              <span className="flex items-center gap-3">
                <span className="text-[11px] text-slate-600 tabular-nums">{items.length}</span>
                <ChevronDown size={15} className={`text-slate-500 transition-transform ${expanded === cat ? 'rotate-180' : ''}`} />
              </span>
            </button>
            <AnimatePresence>
              {expanded === cat && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <div className="divide-y divide-slate-800/60 border-t border-slate-800">
                    {items.map((r, i) => (
                      <button
                        key={i}
                        onClick={() => onPick(r)}
                        className="w-full flex items-center justify-between gap-4 px-5 py-3 text-left hover:bg-indigo-500/[0.06] transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="text-sm text-white font-medium truncate">
                            {r.label || `${r.billing_code_type} ${r.billing_code}`}
                          </div>
                          <div className="text-[11px] text-slate-600 mt-0.5 flex items-center gap-2">
                            <span className="font-mono text-indigo-400">{r.billing_code}</span>
                            {r.is_split && <span className="text-amber-500/80">billed in parts</span>}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-black text-white tabular-nums">
                            {r.min_rate === r.max_rate ? fmt(r.min_rate) : `${fmt(r.min_rate)}–${fmt(r.max_rate)}`}
                          </div>
                          <div className="text-[10px] text-slate-600">
                            {r.min_rate !== r.max_rate ? `median ${fmt(r.median_rate)}` : (r.has_global ? 'full procedure' : 'component only')}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
    </div>
  );
}

function App() {
  const [selectedPlan, setSelectedPlan] = useState('');

  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const [setting, setSetting] = useState('');
  const [npi, setNpi] = useState('');
  const [npiLabel, setNpiLabel] = useState('');

  const [distribution, setDistribution] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedCode, setSelectedCode] = useState(null);
  const [error, setError] = useState(null);

  const [providerRates, setProviderRates] = useState(null);
  const [providerRatesLoading, setProviderRatesLoading] = useState(false);

  const [providerMenu, setProviderMenu] = useState(null);
  const [providerMenuLoading, setProviderMenuLoading] = useState(false);

  const [providerQuote, setProviderQuote] = useState(null);
  const [providerQuoteLoading, setProviderQuoteLoading] = useState(false);

  useEffect(() => {
    // Load network-wide overview immediately on mount
    fetchDistribution(null, null, '', '', '');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Compare-across-providers table — a code is chosen and NO provider filter
  // (with a provider, we show the cost card instead).
  useEffect(() => {
    const code = selectedCode?.code;
    if (!code || npi) { setProviderRates(null); return; }
    let cancelled = false;
    setProviderRatesLoading(true);
    getRatesByProvider(code, selectedCode.type, selectedPlan || undefined, setting || undefined, undefined)
      .then(res => { if (!cancelled) setProviderRates(res.data); })
      .catch(() => { if (!cancelled) setProviderRates(null); })
      .finally(() => { if (!cancelled) setProviderRatesLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, selectedPlan, setting, npi]);

  // Job 1 cost card — a provider AND a specific procedure are both selected.
  useEffect(() => {
    const code = selectedCode?.code;
    if (!npi || !code) { setProviderQuote(null); return; }
    let cancelled = false;
    setProviderQuoteLoading(true);
    getRateQuote(code, selectedCode.type, npi, selectedPlan || undefined)
      .then(res => { if (!cancelled) setProviderQuote(res.data); })
      .catch(() => { if (!cancelled) setProviderQuote(null); })
      .finally(() => { if (!cancelled) setProviderQuoteLoading(false); });
    return () => { cancelled = true; };
  }, [npi, selectedCode, selectedPlan]);

  // Provider "menu" — every procedure the selected provider has a rate for.
  // Shown only when a provider is chosen but no specific procedure is.
  useEffect(() => {
    if (!npi || selectedCode?.code) { setProviderMenu(null); return; }
    let cancelled = false;
    setProviderMenuLoading(true);
    getProviderMenu(npi, selectedPlan || undefined, setting || undefined)
      .then(res => { if (!cancelled) setProviderMenu(res.data); })
      .catch(() => { if (!cancelled) setProviderMenu(null); })
      .finally(() => { if (!cancelled) setProviderMenuLoading(false); });
    return () => { cancelled = true; };
  }, [npi, selectedCode, selectedPlan, setting]);

  // Procedure search. When a provider is selected, scope suggestions to that
  // provider's actual menu (via /providers/{npi}/procedures) so we never offer
  // a procedure the provider doesn't have. Otherwise search the full catalog.
  useEffect(() => {
    if (!isFocused) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }
    const delay = query.length === 0 ? 0 : 300;
    const timer = setTimeout(() => {
      const req = npi
        ? getProviderMenu(npi, selectedPlan || undefined, setting || undefined, query)
            .then(res => (res.data.results || []).slice(0, 20).map(r => ({
              billing_code: r.billing_code,
              billing_code_type: r.billing_code_type,
              label: r.label,
              rbcs_subcategory: r.rbcs_subcategory,
              min_rate: r.min_rate,
              max_rate: r.max_rate,
              n_rates: r.n_rates,
            })))
        : searchBillingCodes(query).then(res => res.data);
      req
        .then(list => { setSuggestions(list); setShowSuggestions(true); })
        .catch(() => {});
    }, delay);
    return () => clearTimeout(timer);
  }, [query, isFocused, npi, selectedPlan, setting]);

  const fetchDistribution = useCallback(async (code, type, planName, activeSetting, activeNpi) => {
    // Provider selected but no procedure yet: that's the "menu" view, handled by
    // its own effect (ProviderMenu). Calling /rates/distribution here would
    // full-scan prices with nothing pruning the code axis — it hangs.
    if (activeNpi && !code) {
      setDistribution(null);
      setSelectedCode(null);
      setError(null);
      setLoading(false);
      setShowSuggestions(false);
      return;
    }
    setLoading(true);
    setError(null);
    setShowSuggestions(false);
    try {
      const res = await getRateDistribution(code || undefined, type || undefined, planName || undefined, activeSetting || undefined, activeNpi || undefined);
      setDistribution(res.data);
      setSelectedCode(code ? { code, type } : null);
    } catch (err) {
      setDistribution(null);
      if (err.response?.status === 404) {
        const scope = planName || 'this network';
        setError(
          activeNpi && !code ? `No negotiated rates for this provider in ${scope}.`
          : activeNpi && code ? `No rates for ${type} ${code} at this provider in ${scope}.`
          : code ? `No rates found for ${type} ${code} in ${scope}.`
          : `No rates found in ${scope}.`
        );
      } else {
        setError('Query failed');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSuggestionClick = (sug) => {
    setQuery(sug.label || cleanProcedureName(sug.name) || `${sug.billing_code} (${sug.billing_code_type})`);
    setShowSuggestions(false);
    fetchDistribution(sug.billing_code, sug.billing_code_type, selectedPlan, setting, npi);
  };

  const handlePlanSelect = (plan) => {
    setSelectedPlan(plan);
    fetchDistribution(selectedCode?.code, selectedCode?.type, plan, setting, npi);
  };

  const handleSettingChange = (s) => {
    setSetting(s);
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, s, npi);
  };

  const handleNpiSelect = (n, label) => {
    setNpi(n);
    setNpiLabel(label || '');
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, setting, n);
  };

  const handleCategoryPick = (term) => {
    setQuery(term);
    setIsFocused(true);
    setShowSuggestions(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // Drill from a provider-menu row into that procedure's full breakdown.
  const handleMenuPick = (row) => {
    setQuery(row.label || `${row.billing_code} (${row.billing_code_type})`);
    fetchDistribution(row.billing_code, row.billing_code_type, selectedPlan, setting, npi);
  };


  const buckets = distribution ? bucketDistribution(distribution.distribution) : [];
  const summary = distribution?.summary;

  const medianBucket = summary && buckets.length > 0
    ? buckets.reduce((prev, curr) =>
        Math.abs(curr.rate_mid - summary.median) < Math.abs(prev.rate_mid - summary.median) ? curr : prev,
        buckets[0])
    : null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30 overflow-x-hidden">
      {/* Header */}
      <nav className="border-b border-white/5 bg-slate-950/80 backdrop-blur-3xl sticky top-0 z-[100]">
        <div className="max-w-5xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 bg-gradient-to-tr from-indigo-700 to-indigo-500 rounded-xl flex items-center justify-center text-white shadow-2xl shadow-indigo-500/20 border border-white/10">
              <ShieldCheck size={24} strokeWidth={3} />
            </div>
            <div className="flex flex-col">
              <span className="text-xl font-black tracking-tighter text-white">HONEST HEALTHCARE</span>
              <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">Anthem Rate Explorer</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <TrendingUp size={16} className="text-indigo-400" />
            <span className="text-xs text-slate-400 font-medium">Georgia Blue Value HMO · MRF Data</span>
          </div>
        </div>
      </nav>

      <main className="max-w-5xl mx-auto px-6 py-16">
        {/* Hero */}
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="mb-12">
          <h1 className="text-6xl font-black text-white mb-4 tracking-tighter leading-[0.9]">
            What does <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-violet-500">your plan</span>
            <br />actually pay?
          </h1>
          <p className="text-slate-400 text-lg max-w-xl leading-relaxed">
            Search any billing code to see the full distribution of negotiated rates across every provider in your network.
          </p>
        </motion.div>

        {/* Search row */}
        <div className="flex flex-col sm:flex-row gap-3 mb-10">
          <NetworkDropdown selectedPlan={selectedPlan} onSelect={handlePlanSelect} />

          {/* Billing code / procedure search */}
          <div className="flex-1 relative bg-slate-900 border border-slate-800 rounded-2xl px-4 flex items-center focus-within:border-indigo-500/50 transition-all">
            <Search className="text-slate-500 shrink-0" size={18} />
            <input
              type="text"
              placeholder="Search procedure or billing code..."
              className="w-full bg-transparent h-14 pl-3 pr-4 outline-none text-white placeholder:text-slate-600 text-sm"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setTimeout(() => { setIsFocused(false); setShowSuggestions(false); }, 200)}
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
                      onClick={() => handleSuggestionClick(sug)}
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
                            ? `${sug.provider_groups.toLocaleString()} groups`
                            : null}
                      </span>
                    </button>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        <CategoryBrowser onPick={handleCategoryPick} />

        {/* Filters row */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-8">
          {/* Setting pills */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest mr-1">Setting</span>
            {[
              { label: 'All', value: '' },
              { label: 'Outpatient', value: 'outpatient' },
              { label: 'Inpatient', value: 'inpatient' },
              { label: 'Ancillary', value: 'ancillary' },
            ].map(({ label, value }) => (
              <button
                key={label}
                onClick={() => handleSettingChange(value)}
                className={`px-3 py-1 rounded-full text-xs font-bold border transition-all ${
                  setting === value
                    ? 'bg-indigo-600 border-indigo-500 text-white'
                    : 'bg-transparent border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Divider */}
          <div className="hidden sm:block w-px h-5 bg-slate-800" />

          {/* NPI filter */}
          <NpiSearch selectedNpi={npi} onSelect={handleNpiSelect} />
        </div>

        {/* Loading */}
        {loading && (
          <div className="py-20 flex flex-col items-center justify-center">
            <div className="w-14 h-14 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mb-5" />
            <div className="text-indigo-400 font-bold tracking-widest uppercase text-[10px] animate-pulse">
              Querying MRF data...
            </div>
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="py-10 text-center text-rose-400 font-medium">{error}</div>
        )}

        {/* Provider menu — provider chosen, no procedure yet */}
        {npi && !selectedCode?.code && !loading && (
          <ProviderMenu
            data={providerMenu}
            loading={providerMenuLoading}
            onPick={handleMenuPick}
            providerName={npiLabel}
            network={selectedPlan}
            onClearNetwork={() => handlePlanSelect('')}
          />
        )}

        {/* Results */}
        <AnimatePresence>
          {distribution && !loading && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
              {/* Negotiated-rate disclaimer */}
              <div className="mb-6 flex items-start gap-2.5 text-xs text-slate-500 bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-3">
                <Info size={14} className="shrink-0 mt-0.5 text-slate-600" />
                <span>
                  These are <span className="text-slate-300 font-semibold">negotiated rates</span> — the price your plan and
                  the provider agreed on, before your benefits apply. What you actually pay depends on your deductible,
                  coinsurance, copay, and out-of-pocket max.
                </span>
              </div>

              {/* Summary stats */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                {[
                  { label: 'Min',    value: fmt(summary.min),    color: 'text-emerald-400' },
                  { label: 'Median', value: fmt(summary.median), color: 'text-indigo-400'  },
                  { label: 'Average',value: fmt(summary.avg),    color: 'text-violet-400'  },
                  { label: 'Max',    value: fmt(summary.max),    color: 'text-rose-400'    },
                ].map(({ label, value, color }) => (
                  <div key={label} className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
                    <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest mb-2">{label}</div>
                    <div className={`text-2xl font-black ${color}`}>{value}</div>
                  </div>
                ))}
              </div>

              {/* Meta row */}
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-6 text-xs text-slate-500">
                {selectedCode?.code
                  ? <><span className="font-mono font-black text-white text-sm">{selectedCode.code}</span>
                      <span className="uppercase font-bold text-slate-600">{selectedCode.type}</span></>
                  : <span className="font-black text-white text-sm">Network Overview</span>
                }
                {summary.n_providers != null && (
                  <span title="Distinct NPIs across these provider groups. Groups are often facility/TIN rollups, so one contract can cover thousands of NPIs.">
                    <span className="text-white font-black">{summary.n_providers.toLocaleString()}</span> providers
                  </span>
                )}
                <span><span className="text-white font-black">{summary.provider_groups?.toLocaleString()}</span> provider groups</span>
                <span><span className="text-white font-black">{summary.total_entries.toLocaleString()}</span> rate entries</span>
                {summary.min > 0 && summary.max / summary.min >= 1.05 && (
                  <span className="text-indigo-400 font-bold">{(summary.max / summary.min).toFixed(1)}× spread</span>
                )}
              </div>

              {/* Histogram */}
              <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8">
                <div className="flex items-center justify-between mb-6">
                  <div>
                    <h2 className="text-white font-black text-xl tracking-tight">Rate Distribution</h2>
                    <p className="text-slate-500 text-xs mt-1">
                      Provider groups per price range ·{' '}
                      <span className="text-indigo-400">— median {fmt(summary.median)}</span>
                    </p>
                  </div>
                  <Activity size={18} className="text-slate-600" />
                </div>
                <ResponsiveContainer width="100%" height={340}>
                  <BarChart data={buckets} margin={{ top: 16, right: 8, left: 0, bottom: 48 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: '#475569', fontSize: 11, fontWeight: 700 }}
                      angle={-35}
                      textAnchor="end"
                      interval={Math.max(0, Math.floor(buckets.length / 8) - 1)}
                    />
                    <YAxis
                      tick={{ fill: '#475569', fontSize: 10, fontWeight: 700 }}
                      width={36}
                      allowDecimals={false}
                    />
                    <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(99,102,241,0.1)' }} />
                    <Bar dataKey="provider_groups" fill="#6366f1" radius={[4, 4, 0, 0]} />
                    {medianBucket && (
                      <ReferenceLine
                        x={medianBucket.label}
                        stroke="#818cf8"
                        strokeDasharray="4 3"
                        strokeWidth={2}
                      />
                    )}
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Provider + procedure both chosen → the cost answer (job 1).
                  Procedure only → the compare-across-providers table (job 3). */}
              {selectedCode?.code && npi && (
                <ProviderCostCard
                  data={providerQuote}
                  loading={providerQuoteLoading}
                  providerName={npiLabel}
                />
              )}
              {selectedCode?.code && !npi && (
                <ProviderRateTable data={providerRates} loading={providerRatesLoading} />
              )}
            </motion.div>
          )}
        </AnimatePresence>

      </main>
    </div>
  );
}

export default App;
