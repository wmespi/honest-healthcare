import { useState } from 'react';
import { MapPin, Search, ShieldCheck } from 'lucide-react';
import { SuggestInput } from './SuggestInput';

const ZIP = /^\d{5}$/;
const normalize = (text) => text.trim().toLowerCase();

const suggest = (items, text, haystack, option) => {
  const query = normalize(text ?? '');
  return items.filter((item) => normalize(haystack(item)).includes(query)).map(option);
};

// `compact` is the results header: choices apply as soon as they're made. Otherwise
// this is the start card, and nothing happens until the button is pressed.
export function SearchFields({ services, plans, serviceId, zip, planId, compact, onSearch }) {
  const initialService = services.find((s) => s.service_id === serviceId) ?? null;
  const initialPlan = plans.find((p) => p.plan_id === planId) ?? null;
  const [service, setService] = useState(initialService);
  const [serviceText, setServiceText] = useState(initialService?.name ?? '');
  const [zipText, setZipText] = useState(zip ?? '');
  const [plan, setPlan] = useState(initialPlan);
  const [planText, setPlanText] = useState(initialPlan?.name ?? '');
  const [error, setError] = useState(null);

  const submit = (nextService, nextZip, nextPlan) => {
    if (!nextService) {
      setError({ field: 'service', message: 'Pick a scan from the list.' });
    } else if (!ZIP.test(nextZip.trim())) {
      setError({ field: 'zip', message: 'Enter a five-digit ZIP code, such as 30306.' });
    } else if (!nextPlan) {
      setError({ field: 'plan', message: 'Pick your plan from the list, since prices depend on it.' });
    } else {
      setError(null);
      onSearch({ serviceId: nextService.service_id, zip: nextZip.trim(), planId: nextPlan.plan_id });
    }
  };

  const fields = (
    <>
      <SuggestInput
        className="col-span-2"
        label="What scan do you need?"
        hideLabel={compact}
        icon={Search}
        placeholder="MRI, CT, X-ray..."
        text={serviceText}
        options={suggest(services, serviceText === service?.name ? '' : serviceText,
          (s) => `${s.name} ${s.keywords}`, (s) => ({ id: s.service_id, label: s.name }))}
        invalid={error?.field === 'service'}
        onTextChange={setServiceText}
        onPick={(option) => {
          const picked = services.find((s) => s.service_id === option.id);
          setService(picked);
          setServiceText(picked.name);
          setError(null);
          if (compact) submit(picked, zipText, plan);
        }}
        onBlur={() => setServiceText(service?.name ?? '')}
      />
      <SuggestInput
        label="What's your ZIP code?"
        hideLabel={compact}
        icon={MapPin}
        placeholder="ZIP code"
        inputMode="numeric"
        text={zipText}
        options={[]}
        invalid={error?.field === 'zip'}
        onTextChange={setZipText}
        onPick={() => {}}
        onEnter={() => submit(service, zipText, plan)}
        onBlur={() => {
          // Clicking away from the header applies a ZIP code and undoes anything else,
          // so the box never disagrees with the results below it.
          if (!compact || zipText === zip) return;
          if (ZIP.test(zipText.trim())) {
            submit(service, zipText, plan);
          } else {
            setZipText(zip ?? '');
            setError(null);
          }
        }}
      />
      <SuggestInput
        label="What plan do you have?"
        hideLabel={compact}
        icon={ShieldCheck}
        placeholder="Your insurance plan"
        text={planText}
        options={suggest(plans, planText === plan?.name ? '' : planText,
          (p) => `${p.name} ${p.carrier}`, (p) => ({ id: p.plan_id, label: p.name, hint: p.carrier }))}
        invalid={error?.field === 'plan'}
        listClassName={compact ? 'left-auto w-[calc(100vw-2rem)] sm:w-96' : undefined}
        onTextChange={setPlanText}
        onPick={(option) => {
          const picked = plans.find((p) => p.plan_id === option.id);
          setPlan(picked);
          setPlanText(picked.name);
          setError(null);
          if (compact) submit(service, zipText, picked);
        }}
        onBlur={() => setPlanText(plan?.name ?? '')}
        onEnter={() => submit(service, zipText, plan)}
      />
    </>
  );

  if (compact) {
    return (
      <div className="relative grid min-w-0 flex-1 grid-cols-2 gap-2 sm:flex">
        {fields}
        {error && (
          <p role="alert" className="absolute left-0 top-full z-20 mt-1.5 rounded-lg bg-rose-50 px-3 py-1.5 text-sm text-rose-800 shadow-md ring-1 ring-rose-200">
            {error.message}
          </p>
        )}
      </div>
    );
  }

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit(service, zipText, plan);
      }}
      className="flex flex-col gap-4"
    >
      {fields}
      {error && <p role="alert" className="text-sm text-rose-700">{error.message}</p>}
      <button
        type="submit"
        className="mt-1 h-12 rounded-xl bg-emerald-700 text-[15px] font-semibold text-white transition-colors hover:bg-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
      >
        See prices
      </button>
    </form>
  );
}
