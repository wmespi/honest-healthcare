import React, { useState, useEffect } from 'react';
import { getHospitals, getRates } from './api';
import { Search, Hospital, ShieldCheck, Info, ChevronRight, Activity } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const CostRangeBar = ({ min, max, median, globalMax }) => {
  const minPercent = (min / globalMax) * 100;
  const maxPercent = (max / globalMax) * 100;
  const medianPercent = (median / globalMax) * 100;

  return (
    <div className="relative w-full h-12 bg-slate-800/50 rounded-full mt-4 overflow-hidden border border-slate-700">
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
      <div className="absolute top-0 bottom-0 left-0 right-0 flex justify-between items-center px-4 pointer-events-none">
        <span className="text-[10px] text-slate-400 font-mono">${min.toLocaleString()}</span>
        <span className="text-[10px] text-slate-400 font-mono">${max.toLocaleString()}</span>
      </div>
    </div>
  );
};

const RateCard = ({ rate, globalMax }) => (
  <motion.div
    layout
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, scale: 0.95 }}
    className="bg-slate-900 border border-slate-800 rounded-2xl p-6 hover:border-indigo-500/50 transition-all shadow-xl group"
  >
    <div className="flex justify-between items-start mb-4">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center text-indigo-400 group-hover:bg-indigo-500 group-hover:text-white transition-colors">
          <Hospital size={20} />
        </div>
        <div>
          <h3 className="text-white font-semibold text-lg leading-tight">{rate.hospital_name}</h3>
          <p className="text-slate-400 text-sm mt-1">{rate.payer} • {rate.plan}</p>
        </div>
      </div>
      <div className="text-right">
        <div className="text-2xl font-bold text-white tracking-tight">${rate.median_rate.toLocaleString()}</div>
        <div className="text-[10px] text-indigo-400 font-bold uppercase tracking-widest mt-1">Estimated Cost</div>
      </div>
    </div>

    <CostRangeBar
      min={rate.min_rate}
      max={rate.max_rate}
      median={rate.median_rate}
      globalMax={globalMax}
    />

    <div className="grid grid-cols-2 gap-4 mt-6 pt-6 border-t border-slate-800">
      <div className="flex items-center gap-2">
        <Activity size={14} className="text-slate-500" />
        <span className="text-xs text-slate-400">Code: <span className="text-slate-200 font-mono">{rate.billing_code}</span></span>
      </div>
      <div className="flex items-center gap-2 justify-end">
        <ShieldCheck size={14} className="text-indigo-400" />
        <span className="text-xs text-slate-400">{rate.record_count} Records</span>
      </div>
    </div>
  </motion.div>
);

function App() {
  const [hospitals, setHospitals] = useState([]);
  const [selectedHospital, setSelectedHospital] = useState('');
  const [searchCode, setSearchCode] = useState('001'); // Standardize on a common DRG
  const [rates, setRates] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getHospitals().then(res => setHospitals(res.data));
  }, []);

  useEffect(() => {
    fetchRates();
  }, [selectedHospital]);

  const fetchRates = async () => {
    setLoading(true);
    try {
      const res = await getRates(searchCode, selectedHospital);
      setRates(res.data);
    } finally {
      setLoading(false);
    }
  };

  const globalMax = Math.max(...rates.map(r => r.max_rate), 100000);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30">
      {/* Header */}
      <nav className="border-b border-white/5 bg-slate-950/50 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-600/20">
              <ShieldCheck size={24} weight="bold" />
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
          <p className="text-slate-400 text-lg max-w-2xl leading-relaxed mb-10">
            Transparency is healthcare's new standard. We process raw hospital MRFs to help you
            discover price variances for the same procedure across different locations.
          </p>

          <div className="flex flex-col md:flex-row gap-4 p-2 bg-slate-900 border border-slate-800 rounded-3xl shadow-2xl">
            <div className="flex-1 relative">
              <Search className="absolute left-6 top-1/2 -translate-y-1/2 text-slate-500" size={20} />
              <input
                type="text"
                placeholder="Search Billing Code (e.g. 001, 874, APC)"
                className="w-full bg-transparent h-16 pl-16 pr-6 outline-none text-white placeholder:text-slate-600 font-medium"
                value={searchCode}
                onChange={(e) => setSearchCode(e.target.value)}
              />
            </div>
            <div className="h-16 w-px bg-slate-800 hidden md:block" />
            <div className="flex-1 relative">
              <Hospital className="absolute left-6 top-1/2 -translate-y-1/2 text-slate-500" size={20} />
              <select
                className="w-full bg-transparent h-16 pl-16 pr-6 outline-none text-white appearance-none cursor-pointer font-medium"
                value={selectedHospital}
                onChange={(e) => setSelectedHospital(e.target.value)}
              >
                <option value="" className="bg-slate-900">All Emory Hospitals</option>
                {hospitals.map(h => (
                  <option key={h} value={h} className="bg-slate-900">{h}</option>
                ))}
              </select>
            </div>
            <button
              onClick={fetchRates}
              className="bg-indigo-600 hover:bg-indigo-500 text-white px-10 h-16 rounded-2xl font-bold transition-all flex items-center justify-center gap-2 group shadow-lg shadow-indigo-600/30"
            >
              Analyze Rates
              <ChevronRight size={20} className="group-hover:translate-x-1 transition-transform" />
            </button>
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

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <AnimatePresence mode="popLayout">
            {rates.map((rate, idx) => (
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
            <p className="text-slate-500">Try a different billing code or select a specific hospital.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
