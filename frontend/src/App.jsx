import { useState, useEffect, useCallback, useRef } from 'react';
import { getNetworks, searchBillingCodes, getRateDistribution, getRatesByProvider, getRatesByNetwork, getRateQuote, getProviderMenu, searchProviders, getSpecialties, getProcedureCategories, getHealth, getPlans } from './api';
import { Search, ShieldCheck, Activity, Layers, TrendingUp, X, ChevronDown, Info, Building2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { estimate, estimateRange, planIsConfigured, COPAY_BUCKETS, COPAY_LABELS } from './oop';
import { usePlanParams } from './usePlanParams';
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

// Row for one provider in the search dropdown.
function ProviderRow({ s, onPick }) {
  return (
    <button
      onClick={() => onPick(s)}
      className="w-full px-4 py-2.5 text-left hover:bg-white/5 transition-colors border-b border-white/5 last:border-0"
    >
      <div className="flex items-center gap-2">
        <span className={`text-sm font-bold truncate ${s.has_rates ? 'text-white' : 'text-slate-500'}`}>{s.name || s.npi}</span>
        {s.has_rates
          ? <span className="text-[9px] font-black uppercase tracking-wide text-emerald-400 shrink-0">has rates</span>
          : <span className="text-[9px] font-black uppercase tracking-wide text-slate-600 shrink-0">no rate data</span>}
        {s.entity_type === 'organization' && (
          <span className="text-[9px] font-black uppercase tracking-wide text-slate-600 shrink-0">clinic</span>
        )}
      </div>
      <div className={`text-[11px] mt-0.5 truncate ${s.has_rates ? 'text-slate-500' : 'text-slate-600'}`}>
        {[s.specialty, s.city, `NPI ${s.npi}`].filter(Boolean).join(' · ')}
      </div>
    </button>
  );
}

// Specialty scope — a filter, like Setting/Network. Default "All specialties".
// Distinct from picking one Provider (that drills to a single NPI).
function SpecialtyDropdown({ selected, onSelect }) {
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
    const t = setTimeout(() => { getSpecialties(query).then(r => setOpts(r.data || [])).catch(() => {}); }, 200);
    return () => clearTimeout(t);
  }, [query, open]);

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

// Provider filter — one specific NPI by name / number. (Specialty is separate.)
function NpiSearch({ selectedNpi, onSelect }) {
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
      searchProviders(query).then(r => { setProviders(r.data); setOpen(true); }).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [query, isFocused]);

  const pickProvider = (s) => {
    const label = s.name || String(s.npi);
    onSelect(String(s.npi), label);
    setSelectedLabel(label);
    setQuery(''); setOpen(false);
  };
  const clear = () => { onSelect('', ''); setQuery(''); setSelectedLabel(''); };
  const firstNoRates = providers.findIndex(s => !s.has_rates);

  return (
    <div ref={ref} className="flex items-center gap-2">
      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest shrink-0">Provider</span>
      {selectedNpi ? (
        <div className="flex items-center gap-2 bg-slate-900 border border-indigo-500/50 rounded-xl px-3 h-8 max-w-[220px]">
          <span className="text-xs text-indigo-300 font-bold truncate">{selectedLabel || selectedNpi}</span>
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
                        No rate data — {providers.length - firstNoRates} more
                      </div>
                    )}
                    <ProviderRow s={s} onPick={pickProvider} />
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

// Name for one billing practice — the group's tin_value resolved to an org
// name, else a member org / physician name, else the taxonomy, else the raw id.
function providerLabel(r) {
  if (r.practice_name) return titleCaseOrg(r.practice_name);
  const orgs = (r.ga_org_names || []).filter(Boolean);
  if (orgs.length) return titleCaseOrg(orgs[0]);
  const indiv = (r.ga_indiv_names || []).filter(Boolean);
  if (indiv.length) return titleCaseOrg(indiv[0]);
  const tax = (r.ga_taxonomies || []).find(t => t && t !== 'Other');
  if (tax) return tax;
  return `Practice ${r.practice_id}`;
}

// "Does the provider matter?" — Job 3. Blue Value is close to a network-wide fee
// schedule, so for most codes every provider negotiated the same rate and the
// honest answer is "provider choice doesn't change the price". When rates do
// vary, show the ranked list with the named practices surfaced.
function ProviderRateTable({ data, loading, specialty }) {
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
      <h2 className="text-white font-black text-xl tracking-tight">
        Does the provider matter?{specialty && <span className="text-indigo-300 font-bold text-base"> · {specialty}</span>}
      </h2>

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
            {(s.n_practices ?? 0).toLocaleString()} billing practices · {(s.n_providers ?? 0).toLocaleString()} providers
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
                {atModal.some(r => r.practice_name) && (
                  <> — incl. {atModal.filter(r => r.practice_name).slice(0, 3).map(providerLabel).join(', ')}</>
                )}
              </p>
            )}
          </>
        );
      })()}
    </div>
  );
}

// Short, plan-type-aware label for the long MRF network names.
function shortNetwork(name) {
  if (!name) return name;
  if (/blue value/i.test(name)) return 'Blue Value (HMO)';
  if (/traditional/i.test(name)) return 'Traditional (PPO)';
  if (/\bHBP\b/i.test(name)) return 'HBP Specialties';
  return name.replace(/^GA\s+/, '');
}

// Job 2 — the same procedure priced across every network we hold. The headline
// finding is usually "the HMO is cheaper and far more predictable than the PPO".
function NetworkCompare({ data, loading, selectedNetwork, onPickNetwork, plan, rbcsCategory }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Comparing plans…
      </div>
    );
  }
  const nets = data?.networks || [];
  if (nets.length < 2) return null;

  const cheapest = nets[0];
  const others = nets.slice(1);
  const maxMed = Math.max(...nets.map(n => n.median || 0));

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">Does your plan matter?</h2>
      <p className="text-slate-400 text-sm mt-2 leading-relaxed max-w-lg">
        {(() => {
          const dear = others[others.length - 1];
          const mult = cheapest.median > 0 ? (dear.median / cheapest.median) : 1;
          if (mult >= 1.25)
            return <>On <span className="text-white font-semibold">{shortNetwork(cheapest.network_name)}</span> this
              runs about {fmt(cheapest.median)} — roughly {mult.toFixed(1)}× less than {shortNetwork(dear.network_name)}.</>;
          return <>Priced similarly across plans (~{fmt(cheapest.median)}).</>;
        })()}
        {' '}
        {cheapest.spread != null && others.some(n => n.spread >= 1.8) && (
          <>It&rsquo;s also steadier — {shortNetwork(cheapest.network_name)} varies {cheapest.spread}× by provider vs.{' '}
          {Math.max(...others.map(n => n.spread || 0))}× on the PPO plans.</>
        )}
      </p>

      <div className="mt-5 space-y-2.5">
        {nets.map((n, i) => {
          const active = n.network_name === selectedNetwork;
          return (
            <button
              key={i}
              onClick={() => onPickNetwork?.(active ? '' : n.network_name)}
              className={`w-full text-left rounded-2xl border p-4 transition-colors ${
                active ? 'border-indigo-500/60 bg-indigo-500/[0.06]' : 'border-slate-800 bg-slate-950/40 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-bold text-slate-200">
                  {shortNetwork(n.network_name)}
                  {active && <span className="ml-2 text-[10px] font-black uppercase tracking-wide text-indigo-400">your filter</span>}
                </span>
                <span className="text-lg font-black text-white tabular-nums shrink-0">{fmt(n.median)}</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div className="h-full bg-indigo-500/70 rounded-full" style={{ width: `${maxMed ? (n.median / maxMed) * 100 : 0}%` }} />
              </div>
              <EstimateLine className="mt-1.5" est={estimate(n.median, plan, { rbcsCategory })} />
              <div className="mt-2 text-[11px] text-slate-500 flex items-center gap-x-3 flex-wrap">
                <span>typically {fmt(n.typical_low)}–{fmt(n.typical_high)}</span>
                {n.spread != null && (
                  <span className={n.spread >= 2 ? 'text-amber-500/80' : 'text-slate-600'}>
                    {n.spread <= 1.15 ? 'flat rate' : `${n.spread}× provider spread`}
                  </span>
                )}
                <span className="text-slate-600" title="Distinct NPIs contracted for this code in this network, across the provider groups below. Groups are often facility/TIN rollups.">
                  {n.n_providers != null
                    ? <>{n.n_providers.toLocaleString()} providers · {n.n_groups.toLocaleString()} groups</>
                    : <>{n.n_groups.toLocaleString()} groups</>}
                </span>
              </div>
            </button>
          );
        })}
      </div>
      <p className="text-slate-600 text-[11px] mt-4">
        All Anthem networks. Tap a plan to filter the rest of the page to it.
      </p>
    </div>
  );
}

// Job 1 — the cost answer for one procedure at one provider. Shows a headline
// rate (a range when it varies by setting) and the breakdown by component
// (full procedure / professional fee / technical fee) and place of service.
function ProviderCostCard({ data, loading, providerName, plan, rbcsCategory }) {
  if (loading) {
    return (
      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center text-slate-500 text-xs font-bold uppercase tracking-widest animate-pulse">
        Pricing this procedure…
      </div>
    );
  }
  if (!data?.headline) return null;
  const { headline, components, is_component_split, provider, plausibility, tier, medicare_utilization: mu } = data;
  const range = (lo, hi) => (lo === hi ? fmt(lo) : `${fmt(lo)}–${fmt(hi)}`);
  const name = provider?.name || providerName;
  const sub = [provider?.specialty, provider?.address || provider?.city].filter(Boolean).join(' · ');
  // The rate belongs to the billing group, not the individual, when the CMS
  // tier says "group" (no utilization, not typical for the specialty) or the
  // legacy heuristic flags a cross-specialty mismatch.
  const groupRate = tier === 'group' || plausibility === 'unlikely';

  // CMS Medicare Part B evidence (issue #14). mu is null until the utilization
  // file is built; {billed:false} means the file is built but this NPI has no
  // Part B row for this code (weak — <=10-beneficiary rows are dropped, and it
  // misses pediatric / commercial / cash practice).
  const medicareBilled = mu?.billed && (
    <p className="mt-4 flex items-start gap-2 text-sm text-emerald-300/90 leading-relaxed max-w-lg">
      <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400" />
      <span>
        {name || 'This provider'} billed this to Medicare{' '}
        <span className="font-semibold text-emerald-200">
          {mu.tot_srvcs.toLocaleString()} time{mu.tot_srvcs === 1 ? '' : 's'} in {mu.year}
        </span>
        {mu.avg_mdcr_allowed ? <> · Medicare allowed ~{fmt(mu.avg_mdcr_allowed)}</> : null} — so
        this is a procedure they actually perform.
      </span>
    </p>
  );

  const breakdown = (
    <div className="space-y-3">
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
  );

  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
      <h2 className="text-white font-black text-xl tracking-tight">
        {groupRate ? 'Group-contracted rate' : 'Negotiated cost'}
        {name ? <> {groupRate ? 'for' : 'at'} <span className="text-indigo-300">{name}</span></> : ''}
      </h2>
      {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}

      {groupRate ? (
        <>
          <p className="mt-4 text-sm text-slate-300 leading-relaxed max-w-lg">
            This rate is attached to the <span className="font-semibold text-white">billing group</span> {name} is
            listed under — in this network that group spans thousands of practices and many specialties.
            The rate sheet doesn&rsquo;t say which providers in the group actually perform this procedure, and we
            have no record of whether {name} bills it. Treat the numbers below as the <em>group&rsquo;s</em> rate,
            not {name}&rsquo;s.
          </p>
          {mu && !mu.billed && (
            <p className="mt-3 text-xs text-slate-500 leading-relaxed max-w-lg">
              No Medicare Part B claims from {name || 'this provider'} for this code in {mu.year} either —
              though that misses pediatric, commercial, and cash practice.
            </p>
          )}
          <details className="mt-4 group">
            <summary className="text-xs font-bold text-slate-500 cursor-pointer hover:text-slate-300 list-none">
              Show the group rate ▸
            </summary>
            <div className="mt-3 opacity-70">{breakdown}</div>
          </details>
        </>
      ) : (
        <>
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
            <EstimateLine
              className="mt-2"
              est={estimateRange(headline.rate, headline.max_rate, plan, { rbcsCategory })}
            />
          </div>
          {medicareBilled}
          <div className="mt-6">{breakdown}</div>
          {is_component_split && (
            <p className="text-[11px] text-slate-500 mt-4 leading-relaxed">
              Often billed as two line items — a <span className="text-slate-400">professional fee</span> (the physician’s
              reading) and a <span className="text-slate-400">technical fee</span> (the equipment and facility) — which
              together roughly equal the full rate. Which you’re charged depends on where it’s done and who interprets it.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// The provider "menu" — every procedure the selected provider has a negotiated
// rate for, grouped by RBCS category. Shown when a provider is picked but no
// specific procedure. Clicking a row drills into that procedure.
function ProviderMenu({ data, loading, onPick, providerName, network, onClearNetwork, onShowAll, plan }) {
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
          {data?.provider?.is_hospital || data?.provider?.is_clinic
            ? 'This is a clinic or facility — negotiated rates are contracted to individual providers. Search a provider’s name, or use the “specialty” mode.'
            : network
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
        <h2 className="text-white font-black text-xl tracking-tight">
          {data?.provider?.name ? `${data.provider.name} — procedure menu` : 'Procedure menu'}
        </h2>
        <p className="text-slate-500 text-xs mt-1">
          {[data?.provider?.specialty, data?.provider?.address || data?.provider?.city].filter(Boolean).join(' · ')}
          {(data?.provider?.specialty || data?.provider?.city) ? ' · ' : ''}
          {data?.tier === 'plausible' && data?.group_count > 0
            ? <>{rows.length.toLocaleString()} procedures this provider bills or that are typical for their specialty. Tap one for the breakdown.</>
            : <>{rows.length.toLocaleString()} procedures with a negotiated rate. Tap one for the breakdown.</>}
        </p>
      </div>
      {data?.group_rate_only && (
        <p className="mb-4 text-xs text-amber-300/80 leading-relaxed bg-amber-500/[0.06] border border-amber-500/20 rounded-2xl px-4 py-3">
          Every rate below reaches {providerName || 'this provider'} through a shared billing group — none is
          verified to them individually. Treat these as the group’s rates.
        </p>
      )}
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
                            {r.medicare ? (
                              <span className="flex items-center gap-1 text-emerald-500/90" title={`Billed ${r.medicare.tot_srvcs.toLocaleString()} times to Medicare in ${r.medicare.year}`}>
                                <ShieldCheck size={11} /> Medicare
                              </span>
                            ) : r.tier === 'typical' ? (
                              <span className="text-slate-500" title={`Typical for ${data?.specialty || 'this specialty'} — not verified to this provider`}>
                                typical for specialty
                              </span>
                            ) : r.tier === 'group' ? (
                              <span className="text-slate-600" title="Reaches this provider only via a shared billing group">
                                group rate
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-sm font-black text-white tabular-nums">
                            {r.min_rate === r.max_rate ? fmt(r.min_rate) : `${fmt(r.min_rate)}–${fmt(r.max_rate)}`}
                          </div>
                          <EstimateLine className="mt-0.5" est={estimateRange(r.min_rate, r.max_rate, plan, { rbcsCategory: r.rbcs_category })} />
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
      {data?.tier === 'plausible' && data?.group_count > 0 && onShowAll && (
        <button
          onClick={onShowAll}
          className="mt-3 w-full rounded-2xl border border-dashed border-slate-800 px-5 py-3 text-left text-xs text-slate-500 hover:text-slate-300 hover:border-slate-700 transition-colors"
        >
          + {data.group_count.toLocaleString()} more rates contracted to this provider’s billing
          group — not verified to them individually. <span className="text-indigo-400 font-bold">Show all</span>
        </button>
      )}
      {data?.tier === 'all' && !data?.group_rate_only && (
        <p className="mt-3 text-[11px] text-slate-600 px-1">
          Showing all contracted rates, including group fan-out. Rows without a badge reach this
          provider only through a shared billing group.
        </p>
      )}
    </div>
  );
}

// "Your cost sharing" — deductible/coinsurance/copay inputs, persisted to
// localStorage (issue #30). Distinct from the plan *identity* picker in the
// network dropdown (issue #33).
function PlanPanel({ form, setField, setCopay, clear, configured }) {
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

// "You'd pay ≈ $X" line under a rate. `est` is from oop.estimate/estimateRange.
function EstimateLine({ est, className = '' }) {
  if (!est) return null;
  const amt = est.low != null
    ? (Math.round(est.low) === Math.round(est.high) ? fmt(est.low) : `${fmt(est.low)}–${fmt(est.high)}`)
    : fmt(est.amount);
  return (
    <div className={`text-[11px] text-emerald-300/90 ${className}`}>
      You'd pay ≈ <span className="font-bold text-emerald-200">{amt}</span>
      <span className="text-slate-600"> · {est.assumption}</span>
    </div>
  );
}

// Dataset coverage + freshness — so a partial dataset announces itself (issue #32).
function TrustBar({ selectedNetwork }) {
  const [h, setH] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem('hh_trustbar_dismissed') === '1'; } catch { return false; }
  });
  useEffect(() => { getHealth().then(r => setH(r.data)).catch(() => {}); }, []);
  if (dismissed || !h || h.priceable_npis == null) return null;

  const asOf = h.as_of
    ? new Date(h.as_of + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : null;
  const nNet = (h.networks || []).length;
  const allNets = !selectedNetwork;

  return (
    <div className="mb-8 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-2.5 text-[11px] text-slate-500 flex items-start gap-2">
      <Info size={13} className="shrink-0 mt-0.5 text-slate-600" />
      <div className="flex-1 leading-relaxed">
        <span className="text-slate-400 font-semibold">{h.priceable_npis.toLocaleString()}</span> providers ·{' '}
        <span className="text-slate-400 font-semibold">{h.n_codes?.toLocaleString()}</span> billing codes ·{' '}
        {nNet} Anthem network{nNet === 1 ? '' : 's'}{asOf && <> · rates as of <span className="text-slate-400">{asOf}</span></>}
        {allNets && nNet > 1 && (
          <> — <span className="text-amber-400/80">“All Networks” mixes GA Blue Value with national mirror data;
          pick a plan for the Georgia individual-market rates.</span></>
        )}
      </div>
      <button
        onClick={() => { setDismissed(true); try { localStorage.setItem('hh_trustbar_dismissed', '1'); } catch { /* ignore */ } }}
        className="shrink-0 text-slate-600 hover:text-slate-300"
        aria-label="Dismiss"
      ><X size={12} /></button>
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
  const [specialty, setSpecialtyState] = useState('');
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
  const [menuTier, setMenuTier] = useState('plausible'); // 'plausible' | 'all'
  const planParams = usePlanParams();
  const planConfigured = planIsConfigured(planParams.plan);

  const [providerQuote, setProviderQuote] = useState(null);
  const [providerQuoteLoading, setProviderQuoteLoading] = useState(false);

  const [networkCompare, setNetworkCompare] = useState(null);
  const [networkCompareLoading, setNetworkCompareLoading] = useState(false);

  useEffect(() => {
    // Load the network-wide overview immediately on mount.
    fetchDistribution(null, null, '', '', '');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Compare-across-providers table — a code is chosen and NO provider filter
  // (with a provider, we show the cost card instead). Scoped by `specialty`.
  useEffect(() => {
    const code = selectedCode?.code;
    if (!code || npi) { setProviderRates(null); return; }
    let cancelled = false;
    setProviderRatesLoading(true);
    getRatesByProvider(code, selectedCode.type, selectedPlan || undefined, setting || undefined, undefined,
      { specialty: specialty || undefined })
      .then(res => { if (!cancelled) setProviderRates(res.data); })
      .catch(() => { if (!cancelled) setProviderRates(null); })
      .finally(() => { if (!cancelled) setProviderRatesLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, selectedPlan, setting, npi, specialty]);

  // Job 2 — compare the selected procedure across every network.
  useEffect(() => {
    const code = selectedCode?.code;
    if (!code) { setNetworkCompare(null); return; }
    let cancelled = false;
    setNetworkCompareLoading(true);
    getRatesByNetwork(code, selectedCode.type, setting || undefined)
      .then(res => { if (!cancelled) setNetworkCompare(res.data); })
      .catch(() => { if (!cancelled) setNetworkCompare(null); })
      .finally(() => { if (!cancelled) setNetworkCompareLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, setting]);

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
  // Reset to the plausible view whenever the provider / plan / setting changes.
  useEffect(() => { setMenuTier('plausible'); }, [npi, selectedPlan, setting]);

  useEffect(() => {
    if (!npi || selectedCode?.code) { setProviderMenu(null); return; }
    let cancelled = false;
    setProviderMenuLoading(true);
    getProviderMenu(npi, selectedPlan || undefined, setting || undefined, '', menuTier)
      .then(res => { if (!cancelled) setProviderMenu(res.data); })
      .catch(() => { if (!cancelled) setProviderMenu(null); })
      .finally(() => { if (!cancelled) setProviderMenuLoading(false); });
    return () => { cancelled = true; };
  }, [npi, selectedCode, selectedPlan, setting, menuTier]);

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

  const fetchDistribution = useCallback(async (code, type, planName, activeSetting, activeNpi, rbcsCategory, activeSpecialty) => {
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
      const res = await getRateDistribution(code || undefined, type || undefined, planName || undefined, activeSetting || undefined, activeNpi || undefined, activeSpecialty || undefined);
      setDistribution(res.data);
      setSelectedCode(code ? { code, type, rbcs_category: rbcsCategory } : null);
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
    fetchDistribution(sug.billing_code, sug.billing_code_type, selectedPlan, setting, npi, sug.rbcs_category, specialty);
  };

  const handlePlanSelect = (plan) => {
    setSelectedPlan(plan);
    fetchDistribution(selectedCode?.code, selectedCode?.type, plan, setting, npi, undefined, specialty);
  };

  const handleSettingChange = (s) => {
    setSetting(s);
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, s, npi, undefined, specialty);
  };

  const handleSpecialtyChange = (sp) => {
    setSpecialtyState(sp);
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, setting, npi, undefined, sp);
  };

  const handleNpiSelect = (n, label) => {
    setNpi(n);
    setNpiLabel(label || '');
    // (specialty stays as a scope; a specific provider just narrows further)
    fetchDistribution(selectedCode?.code, selectedCode?.type, selectedPlan, setting, n, undefined, specialty);
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
    fetchDistribution(row.billing_code, row.billing_code_type, selectedPlan, setting, npi, row.rbcs_category, specialty);
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

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        {/* Hero */}
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="mb-10 sm:mb-12">
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white mb-4 tracking-tighter leading-[0.95] sm:leading-[0.9]">
            What does <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-violet-500">your plan</span>
            <br />actually pay?
          </h1>
          <p className="text-slate-400 text-base sm:text-lg max-w-xl leading-relaxed">
            Search any billing code to see the full distribution of negotiated rates across every provider in your network.
          </p>
        </motion.div>

        <TrustBar selectedNetwork={selectedPlan} />

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

        <PlanPanel
          form={planParams.form}
          setField={planParams.setField}
          setCopay={planParams.setCopay}
          clear={planParams.clear}
          configured={planConfigured}
        />

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

          {/* Specialty scope (a filter) */}
          <SpecialtyDropdown selected={specialty} onSelect={handleSpecialtyChange} />

          {/* Divider */}
          <div className="hidden sm:block w-px h-5 bg-slate-800" />

          {/* One specific provider (a drill-down) */}
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
            onShowAll={() => setMenuTier('all')}
            plan={planParams.plan}
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
                  { label: 'Max',    value: summary.max_capped ? `${fmt(summary.max)}+` : fmt(summary.max), color: 'text-rose-400' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
                    <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest mb-2">{label}</div>
                    <div className={`text-2xl font-black ${color}`}>{value}</div>
                  </div>
                ))}
              </div>

              {specialty && selectedCode?.code && (
                <p className="mb-4 text-xs text-indigo-300/90">
                  Scoped to provider groups that include a <span className="font-semibold">{specialty}</span> provider —
                  the rate still belongs to the whole group.
                </p>
              )}

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
                {summary.provider_groups != null
                  ? <span><span className="text-white font-black">{summary.provider_groups.toLocaleString()}</span> provider groups</span>
                  : summary.n_codes != null && (
                      <span><span className="text-white font-black">{summary.n_codes.toLocaleString()}</span> procedures priced</span>
                    )}
                <span><span className="text-white font-black">{summary.total_entries.toLocaleString()}</span> rate entries</span>
                {summary.min > 0 && !summary.max_capped && summary.max / summary.min >= 1.05 && (
                  <span className="text-indigo-400 font-bold">{(summary.max / summary.min).toFixed(1)}× spread</span>
                )}
              </div>

              {/* Histogram */}
              <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8">
                <div className="flex items-center justify-between mb-6">
                  <div>
                    <h2 className="text-white font-black text-xl tracking-tight">Rate Distribution</h2>
                    <p className="text-slate-500 text-xs mt-1">
                      {selectedCode?.code ? 'Provider groups' : 'Negotiated rate lines'} per price range ·{' '}
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

              {/* Job 2 — the same procedure across every network */}
              {selectedCode?.code && (
                <NetworkCompare
                  data={networkCompare}
                  loading={networkCompareLoading}
                  selectedNetwork={selectedPlan}
                  onPickNetwork={handlePlanSelect}
                  plan={planParams.plan}
                  rbcsCategory={selectedCode?.rbcs_category}
                />
              )}

              {/* Provider + procedure both chosen → the cost answer (job 1).
                  Procedure only → the compare-across-providers table (job 3). */}
              {selectedCode?.code && npi && (
                <ProviderCostCard
                  data={providerQuote}
                  loading={providerQuoteLoading}
                  providerName={npiLabel}
                  plan={planParams.plan}
                  rbcsCategory={selectedCode?.rbcs_category}
                />
              )}
              {selectedCode?.code && !npi && (
                <ProviderRateTable data={providerRates} loading={providerRatesLoading} specialty={specialty} />
              )}
            </motion.div>
          )}
        </AnimatePresence>

      </main>
    </div>
  );
}

export default App;
