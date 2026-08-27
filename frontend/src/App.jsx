import { useState, useEffect, useCallback, useRef } from 'react';
import { getPlans, searchBillingCodes, getRateDistribution, searchProviders } from './api';
import { Search, ShieldCheck, Activity, Layers, TrendingUp, X, ChevronDown } from 'lucide-react';
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

// Searchable plan dropdown — fetches server-side on open and debounces search
function PlanDropdown({ selectedPlan, onSelect }) {
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

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
    if (open && plans.length === 0) getPlans('').then(r => setPlans(r.data)).catch(() => {});
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => {
      getPlans(query).then(r => setPlans(r.data)).catch(() => {});
    }, 250);
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
        <span className="flex-1 text-sm truncate text-white">{selectedPlan || 'All Plans'}</span>
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
                placeholder="Search plans..."
                className="w-full bg-slate-800/80 rounded-xl px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none"
              />
            </div>
            <div className="max-h-60 overflow-y-auto">
              <button
                onClick={() => handleSelect('')}
                className={`w-full px-4 py-3 text-left text-sm flex items-center gap-2 hover:bg-white/5 transition-colors border-b border-white/5 ${!selectedPlan ? 'text-indigo-400' : 'text-slate-400'}`}
              >
                {!selectedPlan && <span>✓</span>}
                <span className={!selectedPlan ? 'font-bold' : ''}>All Plans</span>
              </button>
              {filtered.length === 0 && (
                <div className="px-4 py-3 text-slate-500 text-sm italic">No plans match</div>
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

  const handleSelect = (npi) => {
    onSelect(String(npi));
    setQuery('');
    setShowSuggestions(false);
  };

  const handleClear = () => { onSelect(''); setQuery(''); };

  return (
    <div ref={ref} className="flex items-center gap-2">
      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest shrink-0">NPI</span>
      {selectedNpi ? (
        <div className="flex items-center gap-2 bg-slate-900 border border-indigo-500/50 rounded-xl px-3 h-8">
          <span className="text-xs font-mono text-indigo-400 font-bold">{selectedNpi}</span>
          <button onClick={handleClear} className="text-slate-500 hover:text-white transition-colors"><X size={12} /></button>
        </div>
      ) : (
        <div className="relative">
          <div className={`flex items-center bg-slate-900 border rounded-xl px-3 h-8 gap-2 transition-all ${showSuggestions ? 'border-indigo-500/50' : 'border-slate-800'}`}>
            <input
              type="text"
              inputMode="numeric"
              maxLength={10}
              placeholder="Search NPI…"
              value={query}
              onChange={e => setQuery(e.target.value.replace(/\D/g, ''))}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setTimeout(() => { setIsFocused(false); setShowSuggestions(false); }, 200)}
              className="bg-transparent outline-none text-xs text-white placeholder:text-slate-600 w-28 font-mono"
            />
          </div>
          <AnimatePresence>
            {showSuggestions && suggestions.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                className="absolute top-full left-0 mt-2 bg-slate-900 border border-white/10 rounded-2xl overflow-hidden z-[999] shadow-2xl min-w-[220px] max-h-64 overflow-y-auto"
              >
                {suggestions.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => handleSelect(s.npi)}
                    className="w-full px-4 py-3 text-left hover:bg-white/5 transition-colors border-b border-white/5 last:border-0"
                  >
                    <div className="text-sm font-mono font-bold text-white">{s.npi}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">TIN {s.tin_value}</div>
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

function App() {
  const [selectedPlan, setSelectedPlan] = useState('');

  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const [setting, setSetting] = useState('');
  const [npi, setNpi] = useState('');

  const [distribution, setDistribution] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedCode, setSelectedCode] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Load network-wide overview immediately on mount
    fetchDistribution(null, null, '', '', '');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Show top results immediately on focus; debounce while typing
  useEffect(() => {
    if (!isFocused) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }
    const delay = query.length === 0 ? 0 : 300;
    const timer = setTimeout(() => {
      searchBillingCodes(query)
        .then(res => { setSuggestions(res.data); setShowSuggestions(true); })
        .catch(() => {});
    }, delay);
    return () => clearTimeout(timer);
  }, [query, isFocused]);

  const fetchDistribution = useCallback(async (code, type, planName, activeSetting, activeNpi) => {
    setLoading(true);
    setError(null);
    setShowSuggestions(false);
    try {
      const res = await getRateDistribution(code, type, planName || undefined, activeSetting || undefined, activeNpi || undefined);
      setDistribution(res.data);
      setSelectedCode({ code, type });
    } catch (err) {
      setDistribution(null);
      setError(err.response?.status === 404 ? `No rates found for ${type}:${code}` : 'Query failed');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSuggestionClick = (sug) => {
    setQuery(cleanProcedureName(sug.name) || `${sug.billing_code} (${sug.billing_code_type})`);
    setShowSuggestions(false);
    fetchDistribution(sug.billing_code, sug.billing_code_type, selectedPlan, setting, npi);
  };

  const handlePlanSelect = (plan) => {
    setSelectedPlan(plan);
    if (selectedCode) fetchDistribution(selectedCode.code, selectedCode.type, plan, setting, npi);
  };

  const handleSettingChange = (s) => {
    setSetting(s);
    if (selectedCode) fetchDistribution(selectedCode.code, selectedCode.type, selectedPlan, s, npi);
  };

  const handleNpiSelect = (n) => {
    setNpi(n);
    if (selectedCode) fetchDistribution(selectedCode.code, selectedCode.type, selectedPlan, setting, n);
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
          <PlanDropdown selectedPlan={selectedPlan} onSelect={handlePlanSelect} />

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
                          {cleanProcedureName(sug.name) || sug.billing_code}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[11px] font-mono text-indigo-400">{sug.billing_code}</span>
                          <span className="text-[10px] text-slate-600 uppercase tracking-wide">{sug.billing_code_type}</span>
                        </div>
                      </div>
                      <span className="text-xs text-slate-500 shrink-0 tabular-nums">
                        {sug.provider_groups?.toLocaleString()} groups
                      </span>
                    </button>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

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

        {/* Results */}
        <AnimatePresence>
          {distribution && !loading && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
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
                <span><span className="text-white font-black">{summary.provider_groups}</span> provider groups</span>
                <span><span className="text-white font-black">{summary.total_entries.toLocaleString()}</span> rate entries</span>
                <span className="text-indigo-400 font-bold">{(summary.max / summary.min).toFixed(1)}× spread</span>
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
            </motion.div>
          )}
        </AnimatePresence>

      </main>
    </div>
  );
}

export default App;
