import { useState } from 'react';
import { Building2, MapPin, Search, ShieldCheck } from 'lucide-react';
import { money } from './format';
import { SuggestInput } from './SuggestInput';

const ZIP = /^\d{5}$/;
const normalize = (text) => text.trim().toLowerCase();

const suggest = (items, text, haystack, option) => {
  const query = normalize(text ?? '');
  return items.filter((item) => normalize(haystack(item)).includes(query)).map(option);
};

// A plan by what people know it by, its tier and deductible, then what tells it apart.
const planLabel = (p) => `${p.tier} · ${money(p.deductible)} deductible · ${p.detail}`;
const TIER_BADGE = {
  Platinum: 'bg-indigo-50 text-indigo-800 ring-indigo-200',
  Gold: 'bg-amber-50 text-amber-800 ring-amber-200',
  Silver: 'bg-slate-100 text-slate-700 ring-slate-300',
  Bronze: 'bg-orange-50 text-orange-800 ring-orange-200',
  Catastrophic: 'bg-rose-50 text-rose-800 ring-rose-200',
};
const andList = (items) => (items.length < 2 ? items.join('')
  : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`);

// `compact` is the results header: choices apply as soon as they're made. Otherwise
// this is the start card, and nothing happens until the button is pressed.
export function SearchFields({ services, families = [], plans, serviceId, zip, planId, compact, onSearch }) {
  const initialService = services.find((s) => s.service_id === serviceId) ?? null;
  const initialPlan = plans.find((p) => p.plan_id === planId) ?? null;
  const [service, setService] = useState(initialService);
  const [serviceText, setServiceText] = useState(initialService?.name ?? '');
  // The family whose services the picker lists while nothing is typed; none lists the families.
  const [browsing, setBrowsing] = useState(null);
  const [zipText, setZipText] = useState(zip ?? '');
  const [plan, setPlan] = useState(initialPlan);
  const [planText, setPlanText] = useState(initialPlan ? planLabel(initialPlan) : '');
  const [error, setError] = useState(null);
  // The start card asks for the insurance company first, filled in when there's only one,
  // and offers that company's plans. The header has no room for it, so its plan list names
  // a plan's carrier when the plans don't all share one.
  const carriers = [...new Set(plans.map((p) => p.carrier))];
  const [carrier, setCarrier] = useState(initialPlan?.carrier ?? (carriers.length === 1 ? carriers[0] : null));
  const [carrierText, setCarrierText] = useState(carrier ?? '');
  const planChoices = compact || !carrier ? plans : plans.filter((p) => p.carrier === carrier);

  const familyName = (id) => families.find((f) => f.family === id)?.name;
  const typed = service && serviceText === service.name ? '' : serviceText;
  const serviceOptions = () => {
    if (normalize(typed)) {
      return suggest(services, typed, (s) => `${s.name} ${s.keywords}`,
        (s) => ({ id: `service:${s.service_id}`, label: s.name, hint: familyName(s.family) }));
    }
    if (browsing) {
      return [
        { id: 'back', label: 'Back to all families', keepOpen: true },
        ...services.filter((s) => s.family === browsing)
          .map((s) => ({ id: `service:${s.service_id}`, label: s.name })),
      ];
    }
    return families
      .map((f) => ({ family: f, count: services.filter((s) => s.family === f.family).length }))
      .filter(({ count }) => count > 0)
      .map(({ family, count }) => ({
        id: `family:${family.family}`, label: family.name, detail: `${count} ${count === 1 ? 'service' : 'services'}`, keepOpen: true,
      }));
  };

  const submit = (nextService, nextZip, nextPlan) => {
    if (!nextService) {
      setError({ field: 'service', message: 'Pick a service from the list.' });
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
        label="What care do you need?"
        hideLabel={compact}
        icon={Search}
        placeholder="Office visit, lab test, MRI..."
        text={serviceText}
        options={serviceOptions()}
        invalid={error?.field === 'service'}
        onTextChange={setServiceText}
        onEscape={() => {
          if (!browsing || normalize(typed)) return false;
          setBrowsing(null);
          return true;
        }}
        onPick={(option) => {
          if (option.id === 'back') {
            setBrowsing(null);
            return;
          }
          if (option.id.startsWith('family:')) {
            setBrowsing(option.id.slice('family:'.length));
            return;
          }
          const picked = services.find((s) => s.service_id === option.id.slice('service:'.length));
          setBrowsing(null);
          setService(picked);
          setServiceText(picked.name);
          setError(null);
          if (compact) submit(picked, zipText, plan);
        }}
        onBlur={() => {
          setBrowsing(null);
          setServiceText(service?.name ?? '');
        }}
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
      {!compact && (
        <div>
          <SuggestInput
            label="Who's your insurance company?"
            icon={Building2}
            placeholder="Your insurance company"
            text={carrierText}
            options={suggest(carriers, carrierText === carrier ? '' : carrierText, (c) => c, (c) => ({ id: c, label: c }))}
            onTextChange={setCarrierText}
            onPick={(option) => {
              setCarrier(option.id);
              setCarrierText(option.id);
              if (plan && plan.carrier !== option.id) {
                setPlan(null);
                setPlanText('');
              }
              setError(null);
            }}
            onBlur={() => setCarrierText(carrier ?? '')}
          />
          <p className="mt-1.5 text-[13px] text-slate-500">We have prices for {andList(carriers)} plans so far.</p>
        </div>
      )}
      <SuggestInput
        label="What plan do you have?"
        hideLabel={compact}
        icon={ShieldCheck}
        placeholder={compact || carrier ? 'Your insurance plan' : 'Choose your insurance company first'}
        text={planText}
        options={suggest(planChoices, plan && planText === planLabel(plan) ? '' : planText,
          (p) => `${p.name} ${planLabel(p)} ${p.deductible}`,
          (p) => ({
            id: p.plan_id,
            badge: { text: p.tier, className: TIER_BADGE[p.tier] },
            label: `${money(p.deductible)} deductible`,
            detail: p.detail,
            hint: compact && carriers.length > 1 ? p.carrier : undefined,
          }))}
        invalid={error?.field === 'plan'}
        listClassName={compact ? 'left-auto w-[calc(100vw-2rem)] sm:w-[28rem]' : undefined}
        onTextChange={setPlanText}
        onPick={(option) => {
          const picked = plans.find((p) => p.plan_id === option.id);
          setPlan(picked);
          setPlanText(planLabel(picked));
          setError(null);
          if (compact) submit(service, zipText, picked);
        }}
        onBlur={() => setPlanText(plan ? planLabel(plan) : '')}
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
