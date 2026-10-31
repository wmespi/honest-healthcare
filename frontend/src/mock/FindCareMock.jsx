import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { MotionConfig, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { AboutService } from './AboutService';
import { PriceMap } from './PriceMap';
import { ResultsTable } from './ResultsTable';
import { SearchFields } from './SearchFields';
import { PLANS, SERVICES, SERVICE_DETAILS } from './sampleData';
import { RADIUS_OPTIONS, defaultRadius, findCare, resolveLocation } from './search';
import { miles, money } from './format';

function Brand() {
  return <span className="shrink-0 text-[15px] font-bold tracking-tight text-slate-900">Honest Healthcare</span>;
}

function SampleDataTag() {
  return (
    <span
      title="The practices and prices on this page are made up."
      className="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-900"
    >
      Sample data
    </span>
  );
}

function RadiusPicker({ radiusMi, onChange }) {
  return (
    <div role="radiogroup" aria-label="Search distance" className="flex items-center gap-2">
      <span className="hidden text-[13px] text-slate-500 sm:inline">Within</span>
      <div className="flex rounded-lg bg-slate-100 p-0.5">
        {RADIUS_OPTIONS.map((radius) => (
          <button
            key={radius}
            type="button"
            role="radio"
            aria-checked={radius === radiusMi}
            onClick={() => onChange(radius)}
            className={clsx(
              'rounded-md px-2.5 py-1 text-[13px] font-medium tabular-nums transition-colors focus-visible:outline-2 focus-visible:outline-emerald-700',
              radius === radiusMi ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900',
            )}
          >
            {radius} mi
          </button>
        ))}
      </div>
    </div>
  );
}

function RateExplainer({ plan }) {
  return <>This is the rate {plan.name} negotiated. Depending on your deductible and coinsurance, you may pay less.</>;
}

// The straight answer, before any table: the lowest ceiling nearby, and what it's for.
function Answer({ search, radiusMi, plan, preventive }) {
  const { cheapest, medianPrice, medicareRate, results } = search;
  return (
    <div className="px-4 pb-4 pt-5 sm:px-6">
      <p className="text-[13px] font-medium text-emerald-700">Cheapest within {radiusMi} miles</p>
      <h1 className="mt-1 text-balance text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px] sm:leading-9">
        <span className="tabular-nums">{money(cheapest.price)}</span> is the most you&rsquo;d pay at {cheapest.name}
      </h1>
      <p className="mt-1.5 text-[15px] text-slate-600">
        {miles(cheapest.distanceMi)} away.
        {results.length > 1 && <> The median nearby is {money(medianPrice)}.</>}
        {' '}Medicare pays about {money(medicareRate)}.
      </p>
      <p className="mt-1 hidden text-pretty text-sm text-slate-500 sm:block">
        <RateExplainer plan={plan} />
      </p>
      {preventive && (
        <p className="mt-3 text-pretty rounded-xl bg-emerald-50 px-3.5 py-2.5 text-sm leading-5 text-emerald-900">{preventive}</p>
      )}
    </div>
  );
}

function NothingNearby({ closest, radiusMi, widerRadiusMi, onWiden }) {
  return (
    <div className="px-4 py-10 sm:px-6">
      <h1 className="text-xl font-bold tracking-tight text-slate-900">Nothing within {radiusMi} miles</h1>
      <p className="mt-1 text-[15px] text-slate-600">
        The closest is {closest.name}, {miles(closest.distanceMi)} away, where the most you&rsquo;d pay is {money(closest.price)}.
      </p>
      {widerRadiusMi && (
        <button
          type="button"
          onClick={() => onWiden(widerRadiusMi)}
          className="mt-4 h-10 rounded-xl bg-emerald-700 px-4 text-sm font-semibold text-white transition-colors hover:bg-emerald-800"
        >
          Show within {widerRadiusMi} miles
        </button>
      )}
    </div>
  );
}

export default function FindCareMock() {
  const [params, setParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState(null);
  const [hoveredId, setHoveredId] = useState(null);

  const serviceId = params.get('service');
  const location = params.get('loc');
  const planId = params.get('plan');
  const sort = params.get('sort') === 'distance' ? 'distance' : 'price';

  const service = SERVICES.find((s) => s.id === serviceId);
  const plan = PLANS.find((p) => p.id === planId);
  const origin = useMemo(() => resolveLocation(location), [location]);
  const hasSearch = Boolean(service && plan && origin);

  const chosenRadius = Number(params.get('r'));
  const radiusMi = useMemo(() => {
    if (RADIUS_OPTIONS.includes(chosenRadius)) return chosenRadius;
    return hasSearch ? defaultRadius({ serviceId, origin }) : RADIUS_OPTIONS[1];
  }, [chosenRadius, hasSearch, serviceId, origin]);

  const search = useMemo(
    () => (hasSearch ? findCare({ serviceId, planId, origin, radiusMi, sort }) : null),
    [hasSearch, serviceId, planId, origin, radiusMi, sort],
  );

  const update = (changes) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) => {
        if (value === null) next.delete(key);
        else next.set(key, value);
      });
      return next;
    });
  };
  const applySearch = ({ serviceId: nextService, location: nextLocation, planId: nextPlan }) => {
    setSelectedId(null);
    // A new service or place starts from its own default area, not the last one chosen.
    const sameArea = nextService === serviceId && nextLocation === location;
    update({ service: nextService, loc: nextLocation, plan: nextPlan, ...(sameArea ? {} : { r: null }) });
  };
  const toggleSelected = (id) => setSelectedId((current) => (current === id ? null : id));

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-dvh w-screen flex-col bg-white text-slate-900 [color-scheme:light] lg:h-dvh">
        <header className="relative z-20 flex flex-wrap items-center gap-x-4 gap-y-2.5 border-b border-slate-200 bg-white px-4 py-2.5 sm:px-6">
          <Brand />
          {hasSearch && (
            <div className="order-last w-full lg:order-none lg:w-auto lg:flex-1">
              <SearchFields key={`${serviceId}|${location}|${planId}`} compact serviceId={serviceId} location={location} planId={planId} onSearch={applySearch} />
            </div>
          )}
          <div className="ml-auto"><SampleDataTag /></div>
        </header>

        <div className={clsx('relative flex min-h-0 flex-1 flex-col lg:grid', hasSearch ? 'lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]' : 'lg:grid-cols-1')}>
          {hasSearch && (
            <main className="order-2 min-h-0 lg:order-1 lg:overflow-y-auto">
              {search.cheapest ? (
                <>
                  <Answer search={search} radiusMi={radiusMi} plan={plan} preventive={SERVICE_DETAILS[serviceId].preventive} />
                  <AboutService serviceName={service.name} details={SERVICE_DETAILS[serviceId]} plan={plan} />
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 pb-3 sm:px-6">
                    <span className="text-[13px] text-slate-500">
                      {search.results.length} {search.results.length === 1 ? 'provider' : 'providers'}
                      <span className="hidden sm:inline"> on your plan</span>
                    </span>
                    <RadiusPicker radiusMi={radiusMi} onChange={(r) => update({ r })} />
                  </div>
                  <ResultsTable
                    results={search.results}
                    sort={sort}
                    layoutKey={`${serviceId}|${location}|${planId}|${radiusMi}|${sort}`}
                    selectedId={selectedId}
                    highlightId={hoveredId}
                    onSort={(nextSort) => update({ sort: nextSort })}
                    onSelect={toggleSelected}
                    onHover={setHoveredId}
                  />
                  <div className="space-y-1 px-4 py-4 text-[13px] leading-5 text-slate-500 sm:px-6">
                    <p className="sm:hidden"><RateExplainer plan={plan} /></p>
                    <p>Median is the middle price among the providers listed here, so it changes with your search.</p>
                    <p>Medicare is what Medicare pays for the same service. It doesn&rsquo;t change with your search or your plan.</p>
                  </div>
                </>
              ) : (
                <>
                  <NothingNearby
                    closest={search.closestOutside}
                    radiusMi={radiusMi}
                    widerRadiusMi={search.widerRadiusMi}
                    onWiden={(r) => update({ r })}
                  />
                  <div className="px-4 sm:px-6">
                    <RadiusPicker radiusMi={radiusMi} onChange={(r) => update({ r })} />
                  </div>
                </>
              )}
            </main>
          )}

          <div className={clsx(hasSearch ? 'relative order-1 h-[26dvh] min-h-48 lg:order-2 lg:h-auto' : 'absolute inset-0')}>
            <PriceMap
              origin={hasSearch ? origin : null}
              radiusMi={radiusMi}
              results={search?.results ?? []}
              highlightId={hoveredId}
              selectedId={selectedId}
              onHover={setHoveredId}
              onSelect={toggleSelected}
            />
          </div>

          {!hasSearch && (
            <div className="relative z-10 flex flex-1 items-start justify-center bg-white/35 px-4 pb-10 pt-[6vh] sm:items-center sm:pt-0 lg:col-start-1 lg:row-start-1">
              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: 'easeOut' }}
                className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl shadow-slate-900/15 ring-1 ring-slate-900/5 sm:p-8"
              >
                <h1 className="text-balance text-[28px] font-bold leading-tight tracking-tight text-slate-900 sm:text-3xl">
                  Know the most you could pay before you go
                </h1>
                <p className="mb-6 mt-2 text-pretty text-[15px] text-slate-600">
                  Compare what your plan has agreed with each provider near you, and find the cheapest.
                </p>
                <SearchFields serviceId={serviceId} location={location} planId={planId} onSearch={applySearch} />
              </motion.div>
            </div>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
