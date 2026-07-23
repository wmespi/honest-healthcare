import React, { useState, useEffect } from 'react';
import { getHospitals, getRates, getProcedures, getPayers, getPlans } from './api';
import { Search, Hospital, ShieldCheck, Info, ChevronRight, Activity, CreditCard, Layers, ArrowRightLeft } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const safeFormat = (num) => {
  if (num === null || num === undefined || isNaN(num)) return "0";
  return Number(num).toLocaleString();
};

const safeRound = (num) => {
  if (num === null || num === undefined || isNaN(num)) return 0;
  return Math.round(Number(num));
};

const CostRangeBar = ({ min, max, median, globalMax }) => {
  const gMax = Number(globalMax) || 100000;
  const minPercent = Math.max(0, Math.min(100, (Number(min) / gMax) * 100)) || 0;
  const maxPercent = Math.max(0, Math.min(100, (Number(max) / gMax) * 100)) || 0;
  const medianPercent = Math.max(0, Math.min(100, (Number(median) / gMax) * 100)) || 0;

  return (
    <div className="relative w-full">
      <div className="relative w-full h-12 bg-slate-800/50 rounded-full overflow-hidden border border-slate-700">
        <div
          className="absolute h-full bg-indigo-500/30 border-x border-indigo-400/50"
          style={{ left: `${minPercent}%`, width: `${Math.max(0, maxPercent - minPercent)}%` }}
        />
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white rounded-full shadow-[0_0_15px_rgba(255,255,255,0.8)] border-2 border-indigo-500 z-10"
          style={{ left: `calc(${medianPercent}% - 8px)` }}
        />
      </div>
      <div className="flex justify-between mt-2 px-1">
        <span className="text-[10px] text-slate-500 font-bold">$0</span>
        <span className="text-[10px] text-slate-500 font-bold">${safeFormat(max)} MAX</span>
      </div>
    </div>
  );
};

const MarketBenchmark = ({ summary }) => {
  if (!summary) return null;
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-gradient-to-br from-indigo-600 to-violet-700 rounded-[2rem] p-10 mb-12 shadow-2xl relative overflow-hidden group border border-white/10"
    >
      <div className="absolute top-0 right-0 p-8 opacity-10 group-hover:scale-110 transition-transform">
        <Activity size={160} />
      </div>
      <div className="relative z-10 flex flex-col lg:flex-row items-center justify-between gap-10">
        <div>
          <div className="flex items-center gap-2 text-indigo-100 font-bold uppercase tracking-widest text-[10px] mb-3">
            <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Live Market Intelligence
          </div>
          <h2 className="text-4xl font-black text-white tracking-tighter mb-4">Procedure Benchmark</h2>
          <p className="text-indigo-100/70 text-sm leading-relaxed max-w-sm">
            Based on the latest Anthem MRF disclosures, this represents the price distribution for this specific procedure across all indexed facilities.
          </p>
        </div>
        <div className="flex flex-wrap lg:flex-nowrap items-center justify-center gap-8 lg:gap-16 bg-white/10 backdrop-blur-md p-8 rounded-[1.5rem] border border-white/20">
          <div className="text-center min-w-[100px]">
            <div className="text-indigo-200 text-[10px] font-bold uppercase tracking-widest mb-2 opacity-70">Market Min</div>
            <div className="text-2xl font-black text-white">${safeFormat(summary.min_rate)}</div>
          </div>
          <div className="text-center px-10 border-x border-white/10 min-w-[160px]">
            <div className="text-indigo-100 text-[11px] font-black uppercase tracking-[0.2em] mb-3">Average</div>
            <div className="text-5xl font-black text-white tracking-tighter shadow-sm">${safeFormat(safeRound(summary.avg_rate))}</div>
          </div>
          <div className="text-center min-w-[100px]">
            <div className="text-indigo-200 text-[10px] font-bold uppercase tracking-widest mb-2 opacity-70">Market Max</div>
            <div className="text-2xl font-black text-white">${safeFormat(summary.max_rate)}</div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

const RateCard = ({ rate, globalMax }) => (
  <motion.div
    layout
    initial={{ opacity: 0, scale: 0.95 }}
    animate={{ opacity: 1, scale: 1 }}
    exit={{ opacity: 0, scale: 0.95 }}
    className="bg-slate-900 border border-slate-800 rounded-[2rem] p-8 hover:border-indigo-500/50 transition-all shadow-xl group border-l-4 border-l-indigo-500"
  >
    <div className="flex flex-col lg:flex-row justify-between items-start gap-8 mb-8">
      <div className="flex-1">
        <div className="flex items-center gap-4 mb-4">
          <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 flex items-center justify-center text-indigo-400 shadow-inner">
            <Hospital size={24} />
          </div>
          <div>
            <h3 className="text-white font-black text-2xl tracking-tight leading-none mb-1">
              {rate.facility_name}
            </h3>
            <div className="flex items-center gap-3">
              <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">TIN: {rate.tin || 'N/A'}</span>
              <span className="w-1 h-1 rounded-full bg-slate-700" />
              <span className="text-[10px] text-indigo-400 font-bold uppercase tracking-widest">{rate.network_name}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-3 items-center">
          <div className="flex items-center gap-2 bg-indigo-500/10 px-4 py-2 rounded-xl border border-indigo-500/20 text-indigo-400 font-black text-[10px] uppercase tracking-widest shadow-sm">
            <ShieldCheck size={14} />
            <span>{rate.payor}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {(rate.plan_products || []).slice(0, 3).map((plan, i) => (
              <div key={`${plan}-${i}`} className="text-slate-400 text-[10px] font-black bg-slate-800/80 px-4 py-2 rounded-xl border border-slate-700/50 uppercase tracking-tighter hover:bg-slate-700 hover:text-white transition-colors cursor-default shadow-sm">
                {plan}
              </div>
            ))}
            {(rate.plan_products || []).length > 3 && (
              <div className="text-indigo-400 text-[10px] font-black bg-indigo-500/5 px-4 py-2 rounded-xl border border-indigo-500/20 uppercase tracking-tighter">
                +{(rate.plan_products || []).length - 3} More Plans
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="text-right w-full lg:w-auto p-8 bg-slate-950/50 rounded-3xl border border-slate-800/50 shadow-inner">
        <div className="text-5xl font-black text-white tracking-tighter mb-1">${safeFormat(rate.median_rate)}</div>
        <div className="text-[10px] text-indigo-400 font-black uppercase tracking-[0.2em] mb-4">Negotiated Price</div>
        <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-500 font-bold bg-slate-900/50 p-3 rounded-2xl border border-slate-800/50">
          <div className="flex flex-col items-center border-r border-slate-800/50">
            <span className="opacity-40 mb-1">MIN</span>
            <span className="text-white">${safeFormat(rate.min_rate)}</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="opacity-40 mb-1">MAX</span>
            <span className="text-white">${safeFormat(rate.max_rate)}</span>
          </div>
        </div>
      </div>
    </div>

    <div className="mb-8 bg-slate-950/30 p-6 rounded-2xl border border-slate-800/30">
      <div className="text-[10px] text-slate-500 font-black uppercase tracking-[0.2em] mb-4 opacity-50">Local Cost Variance Analyzer</div>
      <CostRangeBar
        min={rate.min_rate}
        max={rate.max_rate}
        median={rate.median_rate}
        globalMax={globalMax}
      />
    </div>

    <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-6 border-t border-slate-800/50">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-slate-800/50 flex items-center justify-center text-slate-400 border border-slate-700/50">
          <Activity size={18} />
        </div>
        <div>
          <div className="text-[10px] text-slate-500 font-black uppercase tracking-tight opacity-50">Clinical Service</div>
          <div className="text-sm text-white font-bold truncate max-w-[280px]">{rate.procedure_name}</div>
        </div>
      </div>
      <div className="flex items-center gap-0 shadow-lg">
        <div className="bg-indigo-600 px-4 py-2.5 rounded-l-2xl border border-indigo-500 flex items-center gap-2">
          <Layers size={12} className="text-indigo-200" />
          <span className="text-[10px] text-white font-black uppercase tracking-widest">{rate.billing_code_type}</span>
        </div>
        <div className="bg-slate-800 px-5 py-2.5 rounded-r-2xl border border-slate-700 border-l-0">
          <span className="text-xs text-white font-black tracking-widest font-mono">{rate.billing_code}</span>
        </div>
      </div>
    </div>
  </motion.div>
);

function App() {
  const [hospitals, setHospitals] = useState([]);
  const [selectedHospital, setSelectedHospital] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [setting, setSetting] = useState('outpatient');
  const [comparisonMode, setComparisonMode] = useState('insurer');
  const [isTestMode, setIsTestMode] = useState(false);

  const [payers, setPayers] = useState([]);
  const [selectedPayer, setSelectedPayer] = useState('anthem');
  const [plans, setPlans] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState('');

  const [rates, setRates] = useState([]);
  const [marketSummary, setMarketSummary] = useState(null);
  const [loading, setLoading] = useState(false);

  // Autocomplete state
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [searchingSuggestions, setSearchingSuggestions] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  useEffect(() => {
    getHospitals().then(res => setHospitals(res.data));
    getPayers().then(res => setPayers(res.data));
  }, []);

  useEffect(() => {
    if (selectedPayer) {
      getPlans(selectedPayer).then(res => setPlans(res.data));
    } else {
      setPlans([]);
    }
    setSelectedPlan('');
  }, [selectedPayer]);

  // Debounced suggestions
  useEffect(() => {
    if (!isFocused) {
      setShowSuggestions(false);
      return;
    }

    const timer = setTimeout(() => {
      setSearchingSuggestions(true);
      setShowSuggestions(true);
      getProcedures(searchTerm, selectedHospital, setting, selectedPayer, selectedPlan, isTestMode)
        .then(res => {
          setSuggestions(res.data);
        })
        .finally(() => setSearchingSuggestions(false));
    }, 300);

    return () => clearTimeout(timer);
  }, [searchTerm, selectedHospital, setting, isFocused, selectedPayer, selectedPlan, isTestMode]);

  const fetchRates = async (term = searchTerm) => {
    if (!term) return;

    setLoading(true);
    try {
      const res = await getRates(term, selectedHospital, setting, selectedPayer, selectedPlan, isTestMode);
      // Harden response parsing
      const newRates = res.data?.rates || [];
      const newSummary = res.data?.market_summary || null;

      setRates(newRates);
      setMarketSummary(newSummary);
      setShowSuggestions(false);
    } catch (err) {
      console.error("Search failed:", err);
      setRates([]);
      setMarketSummary(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSuggestionClick = (sug) => {
    setSearchTerm(sug);
    setShowSuggestions(false);
    fetchRates(sug);
  };

  // Robust calculations with fallbacks
  const safeRates = Array.isArray(rates) ? rates : [];
  const globalMax = safeRates.length > 0
    ? Math.max(...safeRates.map(r => Number(r.max_rate) || 0))
    : 100000;
  const sortedRates = [...safeRates].sort((a, b) => (Number(a.median_rate) || 0) - (Number(b.median_rate) || 0));

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30 overflow-x-hidden">
      {/* Header */}
      <nav className="border-b border-white/5 bg-slate-950/80 backdrop-blur-3xl sticky top-0 z-[100]">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 bg-gradient-to-tr from-indigo-700 to-indigo-500 rounded-xl flex items-center justify-center text-white shadow-2xl shadow-indigo-500/20 border border-white/10">
              <ShieldCheck size={24} strokeWidth={3} />
            </div>
            <div className="flex flex-col">
              <span className="text-xl font-black tracking-tighter text-white">HONEST HEALTHCARE</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">Anthem Discovery</span>
                {isTestMode && <span className="bg-amber-500/10 text-amber-500 text-[8px] font-black px-1.5 py-0.5 rounded border border-amber-500/20">🧪 TEST MODE</span>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-10 text-xs font-black uppercase tracking-[0.1em] text-slate-400">
            <button
              onClick={() => {
                setIsTestMode(!isTestMode);
                setRates([]);
                setMarketSummary(null);
              }}
              className={`flex items-center gap-2 transition-all ${isTestMode ? 'text-amber-500' : 'hover:text-white'}`}
            >
              <Activity size={14} />
              {isTestMode ? 'EXIT Sandbox' : 'Sandbox (Isolated Test)'}
            </button>
            <a href="#" className="hover:text-white transition-colors">Directory</a>
            <button className="bg-white text-slate-950 px-6 py-3 rounded-2xl font-black hover:bg-slate-100 shadow-2xl transition-all active:scale-95 text-[11px]">
              API Access
            </button>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-6 py-16">
        {/* Search Hero */}
        <div className="mb-20">
          <motion.h1
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="text-6xl lg:text-7xl font-black text-white mb-8 tracking-tighter leading-[0.9]"
          >
            Transparent <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-violet-500">Facility Rates</span> <br />
            Unlocked & Optimized.
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.1 }}
            className="text-slate-400 text-xl max-w-2xl leading-relaxed mb-10 font-medium"
          >
            We've exploded millions of NPI rows to bring you facility-specific price benchmarks.
            No more opaque network names—just direct facility comparisons.
          </motion.p>

          {/* Setting toggle hidden for now as we focus on clinical outpatient data */}
          <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-4 mb-8">
            <div className="hidden flex items-center gap-2 bg-slate-900 w-fit p-1 rounded-2xl border border-slate-800 shrink-0">
              <button
                onClick={() => setSetting('inpatient')}
                className={`px-6 py-3 rounded-xl font-bold transition-all text-sm ${setting === 'inpatient' ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/20' : 'text-slate-500 hover:text-white'}`}
              >
                Inpatient
              </button>
              <button
                onClick={() => setSetting('outpatient')}
                className={`px-6 py-3 rounded-xl font-bold transition-all text-sm ${setting === 'outpatient' ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/20' : 'text-slate-500 hover:text-white'}`}
              >
                Outpatient
              </button>
            </div>

            <div className="flex items-center gap-2 bg-slate-900 w-fit p-1 rounded-2xl border border-slate-800 shrink-0">
              <button
                onClick={() => { setComparisonMode('hospital'); setSelectedHospital(''); }}
                className={`px-6 py-3 rounded-xl font-bold transition-all text-sm flex items-center gap-2 ${comparisonMode === 'hospital' ? 'bg-white text-slate-950 shadow-xl' : 'text-slate-500 hover:text-white'}`}
              >
                <Hospital size={16} />
                Compare Hospitals
              </button>
              <button
                onClick={() => { setComparisonMode('insurer'); setSelectedPayer(''); setSelectedPlan(''); }}
                className={`px-6 py-3 rounded-xl font-bold transition-all text-sm flex items-center gap-2 ${comparisonMode === 'insurer' ? 'bg-white text-slate-950 shadow-xl' : 'text-slate-500 hover:text-white'}`}
              >
                <ArrowRightLeft size={16} />
                Compare Insurers
              </button>
            </div>
          </div>

          <div className="space-y-4">
            {/* Dynamic Comparison Selection */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {comparisonMode === 'hospital' ? (
                <>
                  <div className="relative bg-slate-900 border border-slate-800 rounded-3xl p-2 flex items-center group focus-within:border-indigo-500/50 transition-all">
                    <CreditCard className="ml-4 text-slate-500" size={20} />
                    <select
                      className="w-full bg-transparent h-14 pl-4 pr-6 outline-none text-white appearance-none cursor-pointer font-medium text-sm"
                      value={selectedPayer}
                      onChange={(e) => setSelectedPayer(e.target.value)}
                    >
                      <option value="" className="bg-slate-900">1. Select Insurance Provider</option>
                      {payers.map(p => <option key={p} value={p} className="bg-slate-900">{p}</option>)}
                    </select>
                  </div>
                  <div className="relative bg-slate-900 border border-slate-800 rounded-3xl p-2 flex items-center group focus-within:border-indigo-500/50 transition-all">
                    <Layers className="ml-4 text-slate-500" size={20} />
                    <select
                      className="w-full bg-transparent h-14 pl-4 pr-6 outline-none text-white appearance-none cursor-pointer font-medium text-sm disabled:opacity-50"
                      value={selectedPlan}
                      onChange={(e) => setSelectedPlan(e.target.value)}
                      disabled={!selectedPayer}
                    >
                      <option value="" className="bg-slate-900">2. Select Specific Plan</option>
                      {plans.map(p => <option key={p} value={p} className="bg-slate-900">{p}</option>)}
                    </select>
                  </div>
                </>
              ) : (
                <div className="md:col-span-2 relative bg-slate-900 border border-slate-800 rounded-3xl p-2 flex items-center group focus-within:border-indigo-500/50 transition-all">
                  <Hospital className="ml-4 text-slate-500" size={20} />
                  <select
                    className="w-full bg-transparent h-14 pl-4 pr-6 outline-none text-white appearance-none cursor-pointer font-medium text-sm"
                    value={selectedHospital}
                    onChange={(e) => setSelectedHospital(e.target.value)}
                  >
                    <option value="" className="bg-slate-900">Select Hospital to Compare Insurers</option>
                    {hospitals.map(h => <option key={h} value={h} className="bg-slate-900">{h}</option>)}
                  </select>
                </div>
              )}
            </div>

            {/* Final Step: Procedure Search */}
            <div className="flex flex-col md:flex-row gap-4 p-2 bg-slate-900 border border-slate-800 rounded-3xl shadow-2xl relative">
              <div className="flex-1 relative">
                <Search className="absolute left-6 top-1/2 -translate-y-1/2 text-slate-500" size={20} />
                <input
                  type="text"
                  placeholder="3. Search Procedure (e.g. knee, heart, bypass)"
                  className="w-full bg-transparent h-16 pl-16 pr-6 outline-none text-white placeholder:text-slate-600 font-medium"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && fetchRates()}
                  onFocus={() => {
                    setIsFocused(true);
                    setShowSuggestions(true);
                  }}
                  onBlur={() => {
                    setTimeout(() => {
                      setIsFocused(false);
                      setShowSuggestions(false);
                    }, 200);
                  }}
                />

                <AnimatePresence>
                  {showSuggestions && (searchingSuggestions || (Array.isArray(suggestions) && suggestions.length > 0)) && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 10 }}
                      className="absolute top-full left-0 right-0 mt-3 bg-slate-900 border border-white/10 rounded-3xl overflow-hidden z-[999] shadow-[0_20px_50px_rgba(0,0,0,0.5)] backdrop-blur-xl max-h-96 overflow-y-auto"
                    >
                      {searchingSuggestions ? (
                        <div className="px-6 py-6 text-slate-500 flex items-center gap-3 italic">
                          <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                          Analyzing procedure database...
                        </div>
                      ) : (
                        (Array.isArray(suggestions) ? suggestions : []).map((sug, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleSuggestionClick(sug)}
                            className="w-full px-6 py-4 text-left hover:bg-white/5 text-slate-300 hover:text-white transition-colors flex items-center gap-4 group border-b border-white/5 last:border-0"
                          >
                            <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-slate-500 group-hover:text-indigo-400 group-hover:bg-indigo-500/10 shrink-0">
                              <Activity size={16} />
                            </div>
                            <span className="truncate font-medium">{sug}</span>
                          </button>
                        ))
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              <button
                onClick={fetchRates}
                disabled={!searchTerm}
                className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-10 h-16 rounded-22xl font-bold transition-all flex items-center justify-center gap-2 group shadow-lg shadow-indigo-600/30 min-w-[200px]"
              >
                Analyze Rates
                <ChevronRight size={20} className="group-hover:translate-x-1 transition-transform" />
              </button>
            </div>
          </div>
        </div>

        {/* Market Benchmark Hero */}
        <AnimatePresence>
          {marketSummary && !loading && (
            <MarketBenchmark summary={marketSummary} />
          )}
        </AnimatePresence>

        {/* Results Header */}
        <div className="mb-10 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <h2 className="text-3xl font-black text-white flex items-center gap-4">
            Facility Comparison
            <span className="text-[10px] font-black text-slate-500 bg-slate-900 px-4 py-2 rounded-full border border-slate-800 tracking-[0.2em] uppercase">
              {rates.length} Locations Linked
            </span>
          </h2>
          <div className="flex items-center gap-3 bg-indigo-500/5 px-4 py-2 rounded-2xl border border-indigo-500/20">
            <Info size={16} className="text-indigo-400" />
            <span className="text-xs text-slate-400 font-medium">All benchmarks adjusted for Anthem Clinical network variants.</span>
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="py-20 flex flex-col items-center justify-center">
            <div className="w-16 h-16 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mb-6" />
            <div className="text-indigo-400 font-bold tracking-widest uppercase text-[10px] animate-pulse">Analyzing MRF Payload...</div>
          </div>
        )}

        {/* Results Grid */}
        <div className="grid grid-cols-1 gap-10">
          <AnimatePresence mode="popLayout">
            {sortedRates.map((rate, idx) => (
              <RateCard key={`${rate.tin || 'no-tin'}-${rate.network_name}-${idx}`} rate={rate} globalMax={globalMax} />
            ))}
          </AnimatePresence>
        </div>

        {!loading && rates.length === 0 && (
          <div className="py-24 text-center">
            <div className="w-20 h-20 bg-slate-900 rounded-3xl flex items-center justify-center text-slate-700 mx-auto mb-6">
              <Search size={40} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">No Results Found</h3>
            <p className="text-slate-500">Try a different procedure name or select a specific hospital.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
