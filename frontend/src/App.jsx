import React, { useState, useEffect } from 'react';
import { getHospitals, getRates, getProcedures, getPayers, getPlans } from './api';
import { Search, Hospital, ShieldCheck, Info, ChevronRight, Activity, CreditCard, Layers, ArrowRightLeft } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const CostRangeBar = ({ min, max, median, globalMax }) => {
  const minPercent = (min / globalMax) * 100;
  const maxPercent = (max / globalMax) * 100;
  const medianPercent = (median / globalMax) * 100;

  return (
    <div className="relative w-full">
      <div className="relative w-full h-12 bg-slate-800/50 rounded-full overflow-hidden border border-slate-700">
        <div
          className="absolute h-full bg-indigo-500/30 border-x border-indigo-400/50"
          style={{ left: `${minPercent}%`, width: `${maxPercent - minPercent}%` }}
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
        <span className="text-[10px] text-slate-500 font-bold">${max.toLocaleString()} MAX</span>
      </div>
    </div>
  );
};

const RateCard = ({ rate, globalMax }) => (
  <motion.div
    layout
    initial={{ opacity: 0, scale: 0.9 }}
    animate={{ opacity: 1, scale: 1 }}
    exit={{ opacity: 0, scale: 0.9 }}
    className="bg-slate-900 border border-slate-800 rounded-3xl p-8 hover:border-indigo-500/50 transition-all shadow-2xl group flex flex-col justify-between"
  >
    <div className="flex justify-between items-start mb-8">
      <div className="flex-1">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
            <Hospital size={16} />
          </div>
          <h3 className="text-white font-bold text-xl leading-tight">{rate.hospital_name}</h3>
        </div>
        <div className="space-y-1.5 ml-11">
          <div className="flex items-center gap-2 text-indigo-400 font-semibold text-sm">
            <ShieldCheck size={14} />
            <span>{rate.payer}</span>
          </div>
          <div className="text-slate-400 text-xs font-medium ml-5">{rate.plan}</div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-3xl font-black text-white tracking-tighter">${rate.median_rate.toLocaleString()}</div>
        <div className="text-[10px] text-indigo-400 font-bold uppercase tracking-[0.2em] mt-1 text-right">Estimated Cost</div>
        <div className="text-[10px] text-slate-500 font-medium mt-1 uppercase tracking-wider">
          Range: ${rate.min_rate.toLocaleString()} - ${rate.max_rate.toLocaleString()}
        </div>
      </div>
    </div>

    <div className="mb-8 px-2">
      <div className="text-[10px] text-slate-500 font-bold uppercase tracking-widest mb-2 flex justify-between">
        <span>Cost Variance Range</span>
      </div>
      <CostRangeBar
        min={rate.min_rate}
        max={rate.max_rate}
        median={rate.median_rate}
        globalMax={globalMax}
      />
    </div>

    <div className="flex items-center justify-between pt-6 border-t border-slate-800/50">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-slate-400">
          <Activity size={16} />
        </div>
        <div>
          <div className="text-[10px] text-slate-500 font-bold uppercase tracking-tight">Procedure Type</div>
          <div className="text-xs text-white font-semibold truncate max-w-[200px]">{rate.procedure_type}</div>
        </div>
      </div>
      <div className="flex items-center gap-0">
        <div className="bg-slate-800/50 px-3 py-1.5 rounded-l-full border border-slate-700 border-r-0">
          <span className="text-[9px] text-indigo-400 font-bold uppercase tracking-tighter">{rate.billing_code_type}</span>
        </div>
        <div className="bg-slate-800/50 px-3 py-1.5 rounded-r-full border border-slate-700">
          <span className="text-[10px] text-slate-400 font-mono">{rate.billing_code}</span>
        </div>
      </div>
    </div>
  </motion.div>
);

function App() {
  const [hospitals, setHospitals] = useState([]);
  const [selectedHospital, setSelectedHospital] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [setting, setSetting] = useState('inpatient');
  const [comparisonMode, setComparisonMode] = useState('hospital'); // 'hospital' or 'insurer'

  const [payers, setPayers] = useState([]);
  const [selectedPayer, setSelectedPayer] = useState('');
  const [plans, setPlans] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState('');

  const [rates, setRates] = useState([]);
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
      getProcedures(searchTerm, selectedHospital, setting, selectedPayer, selectedPlan)
        .then(res => {
          setSuggestions(res.data);
        })
        .finally(() => setSearchingSuggestions(false));
    }, 300);

    return () => clearTimeout(timer);
  }, [searchTerm, selectedHospital, setting, isFocused, selectedPayer, selectedPlan]);

  const fetchRates = async () => {
    // Requirements: For 'hospital' mode, need payer/plan/search. For 'insurer' mode, need hospital/search.
    if (!searchTerm) return;

    setLoading(true);
    try {
      const res = await getRates(searchTerm, selectedHospital, setting, selectedPayer, selectedPlan);
      setRates(res.data);
      setShowSuggestions(false);
    } finally {
      setLoading(false);
    }
  };

  const handleSuggestionClick = (sug) => {
    setSearchTerm(sug);
    setShowSuggestions(false);
    // Explicitly fetch results for this suggestion
    setLoading(true);
    getRates(sug, selectedHospital, setting, selectedPayer, selectedPlan)
      .then(res => setRates(res.data))
      .finally(() => setLoading(false));
  };

  const globalMax = rates.length > 0 ? Math.max(...rates.map(r => r.max_rate)) : 100000;
  const sortedRates = [...rates].sort((a, b) => a.median_rate - b.median_rate);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30">
      {/* Header */}
      <nav className="border-b border-white/5 bg-slate-950/50 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-600/20">
              <ShieldCheck size={24} strokeWidth={3} />
            </div>
            <span className="text-xl font-bold tracking-tight text-white italic">Honest Healthcare</span>
          </div>
          <div className="flex items-center gap-8 text-sm font-medium text-slate-400">
            <a href="#" className="hover:text-white transition-colors">Analyzer</a>
            <a href="#" className="hover:text-white transition-colors">Directory</a>
            <button className="bg-white text-slate-950 px-5 py-2.5 rounded-full font-bold hover:bg-indigo-50 shadow-xl transition-all active:scale-95">
              Contact Support
            </button>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-6 py-12">
        {/* Search Hero */}
        <div className="mb-16">
          <h1 className="text-5xl font-extrabold text-white mb-6 tracking-tight">
            Compare <span className="text-indigo-500">Negotiated Rates</span> <br />
            Across the Emory Health System.
          </h1>
          <p className="text-slate-400 text-lg max-w-2xl leading-relaxed mb-6">
            Transparency is healthcare's new standard. We process raw hospital MRFs to help you
            discover price variances for the same procedure across different locations.
          </p>

          <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-4 mb-8">
            <div className="flex items-center gap-2 bg-slate-900 w-fit p-1 rounded-2xl border border-slate-800 shrink-0">
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
                  {showSuggestions && (searchingSuggestions || suggestions.length > 0) && (
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
                        suggestions.map((sug, idx) => (
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

        {/* Results */}
        <div className="mb-8 flex items-center justify-between">
          <h2 className="text-2xl font-bold text-white flex items-center gap-3">
            Search Results
            <span className="text-sm font-normal text-slate-500 bg-slate-900 px-3 py-1 rounded-full border border-slate-800">
              {rates.length} Items Found
            </span>
          </h2>
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Info size={16} />
            Showing ranges for standard insurance plans.
          </div>
        </div>

        <div className="grid grid-cols-1 gap-8">
          <AnimatePresence mode="popLayout">
            {sortedRates.map((rate, idx) => (
              <RateCard key={`${rate.hospital_name}-${idx}`} rate={rate} globalMax={globalMax} />
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
