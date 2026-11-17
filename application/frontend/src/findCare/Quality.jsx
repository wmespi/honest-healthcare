import { clsx } from 'clsx';

// Only CMS's own wording of a comparison is colored, and only when it says better or worse.
const comparedTone = (compared) => clsx(
  compared?.startsWith('Better than') && 'font-medium text-emerald-700',
  compared?.startsWith('Worse than') && 'font-medium text-amber-800',
);

// The first measure, in one line under a row's address.
export function QualityLine({ quality }) {
  const [measure] = quality;
  if (!measure) return null;
  return (
    <div className="mt-0.5 text-[13px] text-slate-500">
      {measure.name}: <span className="whitespace-nowrap">{measure.shown}</span>
      {measure.compared && <>. <span className={comparedTone(measure.compared)}>{measure.compared}</span></>}
    </div>
  );
}

// Every measure that fits the service at a place, with where it comes from.
export function QualityList({ quality }) {
  if (quality.length === 0) return null;
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-slate-900">Quality</h3>
      <ul className="mt-1.5 space-y-2">
        {quality.map((measure) => (
          <li key={measure.measure} className="text-pretty text-sm leading-5 text-slate-600">
            <span className="font-medium text-slate-900">{measure.name}: </span>
            <span className="whitespace-nowrap">{measure.shown}</span>
            {measure.compared && <>. <span className={comparedTone(measure.compared)}>{measure.compared}</span></>}
            <div className="text-[13px] text-slate-500">{measure.period}, {measure.source}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// A clinician's measures, short enough to sit under their specialty.
export function ClinicianQuality({ quality }) {
  return quality.map((measure) => (
    <div key={measure.measure} className="truncate text-[13px] text-slate-500">{measure.name}: {measure.shown}</div>
  ));
}

export function UnmeasuredNote() {
  return (
    <span className="text-[13px] text-slate-500">
      No public quality measure covers this kind of care, so places aren&rsquo;t compared on quality.
    </span>
  );
}
