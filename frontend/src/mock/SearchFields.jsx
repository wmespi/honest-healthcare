import { useState } from 'react';
import { MapPin, Search } from 'lucide-react';
import { SERVICES } from './sampleData';
import { resolveLocation, suggestLocations, suggestServices } from './search';
import { SuggestInput } from './SuggestInput';

const LOCATION_ERROR = "We couldn't place that. This sample covers metro Atlanta, so try a ZIP like 30306.";

// `compact` is the results header: choices apply as soon as they're made. Otherwise
// this is the start card, and nothing happens until the button is pressed.
export function SearchFields({ serviceId, location, compact, onSearch }) {
  const initial = SERVICES.find((s) => s.id === serviceId) ?? null;
  const [picked, setPicked] = useState(initial);
  const [serviceText, setServiceText] = useState(initial?.name ?? '');
  const [locationText, setLocationText] = useState(location ?? '');
  const [error, setError] = useState(null);

  const submit = (service, place) => {
    if (!service) {
      setError({ field: 'service', message: 'Pick a service from the list.' });
    } else if (!resolveLocation(place)) {
      setError({ field: 'location', message: LOCATION_ERROR });
    } else {
      setError(null);
      onSearch({ serviceId: service.id, location: place.trim() });
    }
  };

  const fields = (
    <>
      <SuggestInput
        label="What do you need?"
        hideLabel={compact}
        icon={Search}
        placeholder="MRI, colonoscopy, blood work..."
        text={serviceText}
        options={suggestServices(serviceText === picked?.name ? '' : serviceText)}
        invalid={error?.field === 'service'}
        onTextChange={setServiceText}
        onPick={(option) => {
          const service = SERVICES.find((s) => s.id === option.id);
          setPicked(service);
          setServiceText(service.name);
          setError(null);
          if (compact) submit(service, locationText);
        }}
        onBlur={() => setServiceText(picked?.name ?? '')}
      />
      <SuggestInput
        label="Where are you?"
        hideLabel={compact}
        icon={MapPin}
        placeholder="Address or ZIP code"
        text={locationText}
        options={suggestLocations(locationText === location ? '' : locationText)}
        invalid={error?.field === 'location'}
        onTextChange={setLocationText}
        onPick={(option) => {
          setLocationText(option.label);
          setError(null);
          if (compact) submit(picked, option.label);
        }}
        onEnter={() => submit(picked, locationText)}
      />
    </>
  );

  if (compact) {
    return (
      <div className="relative flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
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
        submit(picked, locationText);
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
