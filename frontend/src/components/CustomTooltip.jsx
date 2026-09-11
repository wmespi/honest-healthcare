// Recharts tooltip for the rate-distribution histogram in routes/Explorer.jsx.
export const CustomTooltip = ({ active, payload, label }) => {
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
