import { money } from './format';

function usual({ low, typical, high }) {
  if (typical === null) return null;
  // A usual range, so whole dollars: cents would claim a precision it doesn't have.
  const dollars = (x) => money(Math.round(x));
  if (low === null || high === null || Math.round(low) === Math.round(high)) return <>usually {dollars(typical)}</>;
  return <>usually {dollars(typical)}, most between {dollars(low)} and {dollars(high)}</>;
}

// Bills a procedure brings that the patient doesn't choose, priced as the plan's usual range
// rather than at a place, so none of it is in the prices below.
export function ExtraBills({ bills }) {
  if (!bills || bills.length === 0) return null;
  return (
    <section aria-label="Also billed separately" className="mx-4 mb-4 rounded-xl border border-slate-200 px-4 py-3 sm:mx-6">
      <h3 className="text-[13px] font-semibold text-slate-900">Also billed separately</h3>
      <p className="mt-0.5 text-[13px] leading-5 text-slate-500">These aren&rsquo;t in the prices below.</p>
      <ul className="mt-2 space-y-2">
        {bills.map((bill) => (
          <li key={bill.part} className="text-sm leading-5 text-slate-900">
            <span className="font-medium">{bill.label}</span>
            {usual(bill) && <>{': '}<span className="tabular-nums">{usual(bill)}</span></>}
            <p className="text-[13px] leading-5 text-slate-500">{bill.note}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
