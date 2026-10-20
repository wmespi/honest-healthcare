import { MapPin } from 'lucide-react';

const RADII = [5, 10, 25, 50];

// "Where are you?" for the PCP picker — a ZIP and a search radius. Optional:
// without a ZIP the list ranks on cost and quality alone; the ZIP only reaches
// the API once it is a full five digits.
export function LocationBar({ zip, onZip, radiusMi, onRadius }) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-slate-400">
      <MapPin size={14} className="text-slate-500" />
      <label className="flex items-center gap-2">
        <span>Your ZIP</span>
        <input
          type="text" inputMode="numeric" maxLength={5} value={zip} placeholder="30309"
          aria-label="Your ZIP code"
          onChange={e => onZip(e.target.value.replace(/\D/g, '').slice(0, 5))}
          className="w-20 bg-slate-900 border border-slate-800 focus:border-indigo-500/60 rounded-lg px-2.5 h-8 text-slate-200 outline-none"
        />
      </label>
      <label className="flex items-center gap-2">
        <span>within</span>
        <select
          value={radiusMi} aria-label="Search radius"
          onChange={e => onRadius(Number(e.target.value))}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2 h-8 text-slate-200 outline-none"
        >
          {RADII.map(r => <option key={r} value={r}>{r} mi</option>)}
        </select>
      </label>
    </div>
  );
}
