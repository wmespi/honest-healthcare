import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { MotionConfig, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { PriceMap } from './PriceMap';
import { ResultsTable } from './ResultsTable';
import { SearchFields } from './SearchFields';
import { SAMPLE_PLAN, SERVICES } from './sampleData';
import { RADIUS_OPTIONS, findCare, resolveLocation } from './search';
import { miles, money } from './format';

const DEFAULT_RADIUS_MI = 10;

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

// The straight answer, before any table.
function Answer({ cheapest, typicalPrice, radiusMi }) {
  const saving = typicalPrice - cheapest.price;
  return (
    <div className="px-4 pb-4 pt-5 sm:px-6">
      <p className="text-[13px] font-medium text-emerald-700">Cheapest within {radiusMi} miles</p>
      <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px] sm:leading-9">
        <span className="tabular-nums">{money(cheapest.price)}</span> at {cheapest.name}
      </h1>
      <p className="mt-1 text-[15px] text-slate-600">
        {miles(cheapest.distanceMi)} away
        {saving > 0 && <>, {money(saving)} below the typical {money(typicalPrice)} nearby</>}
      </p>
    </div>
  );
}

function NothingNearby({ closest, radiusMi, widerRadiusMi, onWiden }) {
  return (
    <div className="px-4 py-10 sm:px-6">
      <h1 className="text-xl font-bold tracking-tight text-slate-900">Nothing within {radiusMi} miles</h1>
      <p className="mt-1 text-[15px] text-slate-600">
        The closest is {closest.name}, {miles(closest.distanceMi)} away at {money(closest.price)}.
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
  const radiusMi = RADIUS_OPTIONS.includes(Number(params.get('r'))) ? Number(params.get('r')) : DEFAULT_RADIUS_MI;
  const sort = params.get('sort') === 'distance' ? 'distance' : 'price';

  const hasService = SERVICES.some((s) => s.id === serviceId);
  const origin = useMemo(() => resolveLocation(location), [location]);
  const hasSearch = hasService && Boolean(origin);

  const search = useMemo(
    () => (hasSearch ? findCare({ serviceId, origin, radiusMi, sort }) : null),
    [hasSearch, serviceId, origin, radiusMi, sort],
  );

  const update = (changes) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) => next.set(key, value));
      return next;
    });
  };
  const applySearch = ({ serviceId: nextService, location: nextLocation }) => {
    setSelectedId(null);
    update({ service: nextService, loc: nextLocation });
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-dvh w-screen flex-col bg-white text-slate-900 [color-scheme:light] lg:h-dvh">
        <header className="relative z-20 flex flex-wrap items-center gap-x-4 gap-y-2.5 border-b border-slate-200 bg-white px-4 py-2.5 sm:px-6">
          <Brand />
          <div className="order-last w-full lg:order-none lg:max-w-2xl lg:flex-1">
            {hasSearch && (
              <SearchFields key={`${serviceId}|${location}`} compact serviceId={serviceId} location={location} onSearch={applySearch} />
            )}
          </div>
          <div className="ml-auto flex items-center gap-3">
            {hasSearch && <span className="hidden text-[13px] text-slate-500 sm:inline">Your plan: <span className="font-medium text-slate-800">{SAMPLE_PLAN}</span></span>}
            <SampleDataTag />
          </div>
        </header>

        <div className={clsx('relative flex min-h-0 flex-1 flex-col lg:grid', hasSearch ? 'lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]' : 'lg:grid-cols-1')}>
          {hasSearch && (
            <main className="order-2 min-h-0 lg:order-1 lg:overflow-y-auto">
              {search.cheapest ? (
                <>
                  <Answer cheapest={search.cheapest} typicalPrice={search.typicalPrice} radiusMi={radiusMi} />
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
                    layoutKey={`${serviceId}|${location}|${radiusMi}|${sort}`}
                    selectedId={selectedId}
                    highlightId={hoveredId}
                    onSort={(nextSort) => update({ sort: nextSort })}
                    onSelect={(id) => setSelectedId((current) => (current === id ? null : id))}
                    onHover={setHoveredId}
                  />
                  <p className="px-4 py-4 text-[13px] leading-5 text-slate-500 sm:px-6">
                    Prices are negotiated rates for {SAMPLE_PLAN}, not your final bill.
                  </p>
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

          <div className={clsx(hasSearch ? 'relative order-1 h-[30dvh] min-h-52 lg:order-2 lg:h-auto' : 'absolute inset-0')}>
            <PriceMap
              origin={hasSearch ? origin : null}
              radiusMi={radiusMi}
              results={search?.results ?? []}
              highlightId={hoveredId}
              selectedId={selectedId}
              onHover={setHoveredId}
              onSelect={(id) => setSelectedId((current) => (current === id ? null : id))}
            />
          </div>

          {!hasSearch && (
            <div className="relative z-10 flex flex-1 items-start justify-center bg-white/35 px-4 pb-10 pt-[9vh] sm:items-center sm:pt-0 lg:col-start-1 lg:row-start-1">
              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: 'easeOut' }}
                className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl shadow-slate-900/15 ring-1 ring-slate-900/5 sm:p-8"
              >
                <h1 className="text-balance text-[28px] font-bold leading-tight tracking-tight text-slate-900 sm:text-3xl">
                  Find the cheapest care near you
                </h1>
                <p className="mb-6 mt-2 text-pretty text-[15px] text-slate-600">
                  See what each provider charges on your plan, side by side.
                </p>
                <SearchFields serviceId={serviceId} location={location} onSearch={applySearch} />
              </motion.div>
            </div>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
