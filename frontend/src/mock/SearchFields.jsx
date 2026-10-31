import { useState } from 'react';
import { MapPin, Search, ShieldCheck } from 'lucide-react';
import { PLANS, SERVICES } from './sampleData';
import { resolveLocation, suggestLocations, suggestPlans, suggestServices } from './search';
import { SuggestInput } from './SuggestInput';

const LOCATION_ERROR = 'We couldn’t place that. This sample covers metro Atlanta, so try a ZIP like 30306.';

// `compact` is the results header: choices apply as soon as they're made. Otherwise
// this is the start card, and nothing happens until the button is pressed.
export function SearchFields({ serviceId, location, planId, compact, onSearch }) {
  const initialService = SERVICES.find((s) => s.id === serviceId) ?? null;
  const initialPlan = PLANS.find((p) => p.id === planId) ?? null;
  const [service, setService] = useState(initialService);
  const [serviceText, setServiceText] = useState(initialService?.name ?? '');
  const [locationText, setLocationText] = useState(location ?? '');
  const [plan, setPlan] = useState(initialPlan);
  const [planText, setPlanText] = useState(initialPlan?.name ?? '');
  const [error, setError] = useState(null);

  const submit = (nextService, place, nextPlan) => {
    if (!nextService) {
      setError({ field: 'service', message: 'Pick a service from the list.' });
    } else if (!resolveLocation(place)) {
      setError({ field: 'location', message: LOCATION_ERROR });
    } else if (!nextPlan) {
      setError({ field: 'plan', message: 'Pick your plan from the list, since prices depend on it.' });
    } else {
      setError(null);
      onSearch({ serviceId: nextService.id, location: place.trim(), planId: nextPlan.id });
    }
  };

  const fields = (
    <>
      <SuggestInput
        className="col-span-2"
        label="What do you need?"
        hideLabel={compact}
        icon={Search}
        placeholder="MRI, colonoscopy, blood work..."
        text={serviceText}
        options={suggestServices(serviceText === service?.name ? '' : serviceText)}
        invalid={error?.field === 'service'}
        onTextChange={setServiceText}
        onPick={(option) => {
          const picked = SERVICES.find((s) => s.id === option.id);
          setService(picked);
          setServiceText(picked.name);
          setError(null);
          if (compact) submit(picked, locationText, plan);
        }}
        onBlur={() => setServiceText(service?.name ?? '')}
      />
      <SuggestInput
        label="Where are you?"
        hideLabel={compact}
        icon={MapPin}
        placeholder="Address or ZIP code"
        text={locationText}
        options={suggestLocations(locationText === location ? '' : locationText)}
        invalid={error?.field === 'location'}
        listClassName={compact ? 'w-[calc(100vw-2rem)] sm:w-auto' : undefined}
        onTextChange={setLocationText}
        onPick={(option) => {
          setLocationText(option.label);
          setError(null);
          if (compact) submit(service, option.label, plan);
        }}
        onEnter={() => submit(service, locationText, plan)}
        onBlur={() => {
          // Clicking away from the header applies a real place and undoes anything else,
          // so the box never disagrees with the results below it.
          if (!compact || locationText === location) return;
          if (resolveLocation(locationText)) {
            submit(service, locationText, plan);
          } else {
            setLocationText(location ?? '');
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
        options={suggestPlans(planText === plan?.name ? '' : planText)}
        invalid={error?.field === 'plan'}
        listClassName={compact ? 'left-auto w-[calc(100vw-2rem)] sm:w-72' : undefined}
        onTextChange={setPlanText}
        onPick={(option) => {
          const picked = PLANS.find((p) => p.id === option.id);
          setPlan(picked);
          setPlanText(picked.name);
          setError(null);
          if (compact) submit(service, locationText, picked);
        }}
        onBlur={() => setPlanText(plan?.name ?? '')}
        onEnter={() => submit(service, locationText, plan)}
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
        submit(service, locationText, plan);
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
