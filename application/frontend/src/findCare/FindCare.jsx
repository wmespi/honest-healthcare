import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { MotionConfig, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { Search, X } from 'lucide-react';
import { getFindCare, getPlans, getServices } from '../api';
import { AboutService } from './AboutService';
import { PriceMap } from './PriceMap';
import { ResultsTable } from './ResultsTable';
import { SearchFields } from './SearchFields';
import { miles, money } from './format';
import { filterResults, searchView } from './view';

// The radii the contract offers (application/serving/contract.py RADIUS_OPTIONS_MI).
const RADIUS_OPTIONS = [5, 10, 25, 50];
const ZIP = /^\d{5}$/;

function Brand() {
  return <span className="shrink-0 text-[15px] font-bold tracking-tight text-slate-900">Honest Healthcare</span>;
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
  return (
    <>
      Each price is a rate your plan has agreed with that place. Where it has agreed several,
      you see the one for the doctors known to do this service there. Your plan has
      a {money(plan.deductible)} deductible, and each place shows your share.
    </>
  );
}

function ResultFilter({ text, onChange }) {
  return (
    <div className="relative order-last w-full sm:order-none sm:w-auto sm:min-w-0 sm:max-w-56 sm:flex-1">
      <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
      <input
        type="search"
        aria-label="Find a place or provider"
        value={text}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Find a place or provider"
        className="h-8 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-8 text-[13px] text-slate-900 placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-emerald-700 [&::-webkit-search-cancel-button]:appearance-none"
      />
      {text && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange('')}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 transition-colors hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-emerald-700"
        >
          <X size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

function PreventiveNote({ children }) {
  return <p className="mt-3 text-pretty rounded-xl bg-emerald-50 px-3.5 py-2.5 text-sm leading-5 text-emerald-900">{children}</p>;
}

function WidenButton({ radiusMi, onWiden }) {
  return (
    <button
      type="button"
      onClick={() => onWiden(radiusMi)}
      className="mt-4 h-10 rounded-xl bg-emerald-700 px-4 text-sm font-semibold text-white transition-colors hover:bg-emerald-800"
    >
      Show within {radiusMi} miles
    </button>
  );
}

function MedicareRate({ rate }) {
  return rate === null ? null : <> Medicare pays about {money(rate)}.</>;
}

// Places nearby, but none we can give a ceiling for, so the answer says that instead of naming one.
function NoFullPrice({ search, radiusMi, plan, preventive, onWiden }) {
  const { medicareRate, results, widerRadiusMi } = search;
  return (
    <div className="px-4 pb-4 pt-5 sm:px-6">
      <p className="text-[13px] font-medium text-amber-800">No full price within {radiusMi} miles</p>
      <h1 className="mt-1 text-balance text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px] sm:leading-9">
        We can&rsquo;t say the most you&rsquo;d pay nearby
      </h1>
      <p className="mt-1.5 text-[15px] text-slate-600">
        {results.length === 1 ? 'The one place nearby is missing part of its bill.' : `All ${results.length} places nearby are missing part of their bill.`}
        <MedicareRate rate={medicareRate} />
      </p>
      <p className="mt-1 hidden text-pretty text-sm text-slate-500 sm:block">
        <RateExplainer plan={plan} />
      </p>
      {preventive && <PreventiveNote>{preventive}</PreventiveNote>}
      {widerRadiusMi && <WidenButton radiusMi={widerRadiusMi} onWiden={onWiden} />}
    </div>
  );
}

// The straight answer, before any table: the lowest ceiling nearby, and what it's for.
function Answer({ search, radiusMi, plan, preventive }) {
  const { cheapest, nearbyMedian, medicareRate, pricedCount } = search;
  return (
    <div className="px-4 pb-4 pt-5 sm:px-6">
      <p className="text-[13px] font-medium text-emerald-700">Cheapest within {radiusMi} miles</p>
      <h1 className="mt-1 text-balance text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px] sm:leading-9">
        <span className="tabular-nums">{money(cheapest.maxRate)}</span> is the most you&rsquo;d pay at {cheapest.name}
      </h1>
      <p className="mt-1.5 text-[15px] text-slate-600">
        {miles(cheapest.distanceMi)} away.
        {pricedCount > 1 && <> The median nearby is {money(nearbyMedian)}.</>}
        <MedicareRate rate={medicareRate} />
      </p>
      <p className="mt-1 hidden text-pretty text-sm text-slate-500 sm:block">
        <RateExplainer plan={plan} />
      </p>
      {preventive && <PreventiveNote>{preventive}</PreventiveNote>}
    </div>
  );
}

function NothingNearby({ closest, radiusMi, widerRadiusMi, service, plan, onWiden }) {
  if (!closest) {
    return (
      <div className="px-4 py-10 sm:px-6">
        <h1 className="text-xl font-bold tracking-tight text-slate-900">No prices for this scan on your plan</h1>
        <p className="mt-1 text-pretty text-[15px] text-slate-600">
          {plan.name}&rsquo;s file doesn&rsquo;t list a price for {service.name.toLowerCase()} at any place in Georgia we can put on the map.
        </p>
      </div>
    );
  }
  return (
    <div className="px-4 py-10 sm:px-6">
      <h1 className="text-xl font-bold tracking-tight text-slate-900">Nothing within {radiusMi} miles</h1>
      <p className="mt-1 text-[15px] text-slate-600">
        The closest is {closest.name}, {miles(closest.distanceMi)} away,
        {closest.maxRate === null ? ' but its full price isn’t on file.' : <> where the most you&rsquo;d pay is {money(closest.maxRate)}.</>}
      </p>
      {widerRadiusMi && <WidenButton radiusMi={widerRadiusMi} onWiden={onWiden} />}
    </div>
  );
}

function Message({ title, children }) {
  return (
    <div className="px-4 py-10 sm:px-6">
      <h1 className="text-xl font-bold tracking-tight text-slate-900">{title}</h1>
      {children && <p className="mt-1 text-pretty text-[15px] text-slate-600">{children}</p>}
    </div>
  );
}

function Results({ search, service, plan, sort, filterText, setFilterText, selectedId, hoveredId, onSelect, onHover, update, layoutKey }) {
  const radiusMi = search.radiusMi;
  const shown = filterResults(search.results, filterText);
  const preventive = service.preventive_note;
  if (search.results.length === 0) {
    return (
      <>
        <NothingNearby
          closest={search.closestOutside}
          radiusMi={radiusMi}
          widerRadiusMi={search.widerRadiusMi}
          service={service}
          plan={plan}
          onWiden={(r) => update({ r })}
        />
        {search.closestOutside && (
          <div className="px-4 sm:px-6">
            <RadiusPicker radiusMi={radiusMi} onChange={(r) => update({ r })} />
          </div>
        )}
      </>
    );
  }
  return (
    <>
      {search.cheapest
        ? <Answer search={search} radiusMi={radiusMi} plan={plan} preventive={preventive} />
        : <NoFullPrice search={search} radiusMi={radiusMi} plan={plan} preventive={preventive} onWiden={(r) => update({ r })} />}
      <AboutService service={service} plan={plan} />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 pb-3 sm:px-6">
        <span className="text-[13px] text-slate-500">
          {shown.length !== search.results.length && `${shown.length} of `}
          {search.results.length} {search.results.length === 1 ? 'place' : 'places'}
          <span className="hidden sm:inline"> on your plan</span>
        </span>
        <ResultFilter text={filterText} onChange={setFilterText} />
        <RadiusPicker radiusMi={radiusMi} onChange={(r) => update({ r })} />
      </div>
      {shown.length > 0 ? (
        <ResultsTable
          results={shown}
          plan={plan}
          costSharing={search.costSharing}
          sort={sort}
          layoutKey={`${layoutKey}|${filterText}`}
          selectedId={selectedId}
          highlightId={hoveredId}
          onSort={(nextSort) => update({ sort: nextSort })}
          onSelect={onSelect}
          onHover={onHover}
        />
      ) : (
        <p className="border-t border-slate-200 px-4 py-6 text-sm text-slate-600 sm:px-6">
          No place or provider within {radiusMi} miles matches &ldquo;{filterText.trim()}&rdquo;.
        </p>
      )}
      <div className="space-y-1 px-4 py-4 text-[13px] leading-5 text-slate-500 sm:px-6">
        <p className="sm:hidden"><RateExplainer plan={plan} /></p>
        {search.cheapest && <p>Median is the middle of the prices listed here, so it changes with your search.</p>}
        {search.pricedCount < search.results.length && (
          <p>A place missing part of its bill has no &ldquo;most you&rsquo;d pay&rdquo;, so it isn&rsquo;t in the median and comes last when sorted by price.</p>
        )}
        {search.medicareRate !== null && (
          <p>Medicare is what Medicare pays for the same scan. It doesn&rsquo;t change with your search or your plan.</p>
        )}
      </div>
    </>
  );
}

// The answer for one search: the response, or the problem the API named.
function useFindCare(query) {
  const [answer, setAnswer] = useState(null);
  const key = query && JSON.stringify(query);
  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    getFindCare(JSON.parse(key))
      .then((response) => { if (live) setAnswer({ key, body: response.data }); })
      .catch((error) => { if (live) setAnswer({ key, problem: error.response?.data?.detail?.code ?? 'error' }); });
    return () => { live = false; };
  }, [key]);
  // The last answer stays up while the next one loads, so the page doesn't flash.
  return { answer, loading: Boolean(key) && answer?.key !== key };
}

export default function FindCare() {
  const [params, setParams] = useSearchParams();
  const [catalog, setCatalog] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [hoveredId, setHoveredId] = useState(null);
  const [filterText, setFilterText] = useState('');

  useEffect(() => {
    Promise.all([getPlans(), getServices()])
      .then(([plans, services]) => setCatalog({ plans: plans.data, services: services.data }))
      .catch(() => setCatalog({ error: true }));
  }, []);

  const serviceId = params.get('service');
  const zip = params.get('zip');
  const planId = params.get('plan');
  const sort = params.get('sort') === 'distance' ? 'distance' : 'price';
  const chosenRadius = RADIUS_OPTIONS.includes(Number(params.get('r'))) ? Number(params.get('r')) : null;

  const service = catalog?.services?.find((s) => s.service_id === serviceId);
  const plan = catalog?.plans?.find((p) => p.plan_id === planId);
  const hasSearch = Boolean(service && plan && ZIP.test(zip ?? ''));
  const query = hasSearch ? { serviceId, planId, zip, radiusMi: chosenRadius, sort } : null;
  const { answer, loading } = useFindCare(query);

  const search = useMemo(() => {
    if (!answer?.body) return null;
    return { ...searchView(answer.body, service), radiusMi: answer.body.radius_mi, origin: answer.body.origin };
  }, [answer, service]);
  const shownOnMap = useMemo(() => filterResults(search?.results ?? [], filterText), [search, filterText]);

  const update = (changes) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([name, value]) => {
        if (value === null) next.delete(name);
        else next.set(name, value);
      });
      return next;
    });
  };
  const applySearch = ({ serviceId: nextService, zip: nextZip, planId: nextPlan }) => {
    setSelectedId(null);
    setFilterText('');
    // A new service or place starts from its own default area, not the last one chosen.
    const sameArea = nextService === serviceId && nextZip === zip;
    update({ service: nextService, zip: nextZip, plan: nextPlan, ...(sameArea ? {} : { r: null }) });
  };
  const toggleSelected = (id) => setSelectedId((current) => (current === id ? null : id));

  let body = null;
  if (hasSearch && answer?.problem === 'unknown_zip') {
    body = <Message title={`We don’t know the ZIP code ${zip}`}>Check it, or try a nearby one.</Message>;
  } else if (hasSearch && answer?.problem) {
    body = <Message title="We couldn’t load prices">Try again in a moment.</Message>;
  } else if (hasSearch && search) {
    body = (
      <Results
        search={search}
        service={service}
        plan={plan}
        sort={sort}
        filterText={filterText}
        setFilterText={setFilterText}
        selectedId={selectedId}
        hoveredId={hoveredId}
        onSelect={toggleSelected}
        onHover={setHoveredId}
        update={update}
        layoutKey={`${serviceId}|${zip}|${planId}|${search.radiusMi}|${sort}`}
      />
    );
  } else if (hasSearch) {
    body = <Message title="Finding prices…" />;
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-dvh w-screen flex-col bg-white text-slate-900 [color-scheme:light] lg:h-dvh">
        <header className="relative z-20 flex flex-wrap items-center gap-x-4 gap-y-2.5 border-b border-slate-200 bg-white px-4 py-2.5 sm:px-6">
          <Brand />
          {hasSearch && (
            <div className="order-last w-full lg:order-none lg:w-auto lg:flex-1">
              <SearchFields
                key={`${serviceId}|${zip}|${planId}`}
                compact
                services={catalog.services}
                plans={catalog.plans}
                serviceId={serviceId}
                zip={zip}
                planId={planId}
                onSearch={applySearch}
              />
            </div>
          )}
        </header>

        <div className={clsx('relative flex min-h-0 flex-1 flex-col lg:grid', hasSearch ? 'lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]' : 'lg:grid-cols-1')}>
          {hasSearch && (
            <main aria-busy={loading} className={clsx('order-2 min-h-0 transition-opacity lg:order-1 lg:overflow-y-auto', loading && search && 'opacity-60')}>
              {body}
            </main>
          )}

          <div className={clsx(hasSearch ? 'relative order-1 h-[26dvh] min-h-48 lg:order-2 lg:h-auto' : 'absolute inset-0')}>
            <PriceMap
              origin={hasSearch && search ? search.origin : null}
              radiusMi={search?.radiusMi ?? RADIUS_OPTIONS[1]}
              results={hasSearch ? shownOnMap : []}
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
                className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl shadow-slate-900/15 ring-1 ring-slate-900/5 sm:p-8"
              >
                <h1 className="text-balance text-[28px] font-bold leading-tight tracking-tight text-slate-900 sm:text-3xl">
                  Know the most you could pay before you go
                </h1>
                <p className="mb-6 mt-2 text-pretty text-[15px] text-slate-600">
                  Compare what your plan has agreed with imaging centers and hospitals near you, and find the cheapest.
                </p>
                {catalog?.error && <p role="alert" className="text-sm text-rose-700">We couldn&rsquo;t load the scans and plans. Try again in a moment.</p>}
                {catalog?.services && (
                  <SearchFields services={catalog.services} plans={catalog.plans} serviceId={serviceId} zip={zip} planId={planId} onSearch={applySearch} />
                )}
              </motion.div>
            </div>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
