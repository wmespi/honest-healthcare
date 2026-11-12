import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { ArrowDown, Navigation, Phone } from 'lucide-react';
import { miles, money } from './format';
import { shareText } from './view';

const PROVIDERS_SHOWN = 4;

function SortHeader({ label, shortLabel, sortKey, sort, onSort, className }) {
  const active = sort === sortKey;
  return (
    <th scope="col" aria-sort={active ? 'ascending' : 'none'} className={clsx('py-2.5 text-right font-medium', className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={clsx(
          'inline-flex items-center gap-1 whitespace-nowrap rounded transition-colors hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700',
          active && 'text-slate-900',
        )}
      >
        {active && <ArrowDown size={12} strokeWidth={2.5} aria-hidden="true" />}
        {shortLabel ? (
          <>
            <span className="sm:hidden">{shortLabel}</span>
            <span className="hidden sm:inline">{label}</span>
          </>
        ) : label}
      </button>
    </th>
  );
}

function Badge({ tone, children }) {
  return (
    <span
      className={clsx(
        'inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4',
        tone === 'cheapest' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700',
      )}
    >
      {children}
    </span>
  );
}

function PriceComparison({ difference }) {
  if (difference === 0) return <span className="text-slate-500">The median price</span>;
  if (difference < 0) return <span className="font-medium text-emerald-700">{money(-difference)} below median</span>;
  return <span className="text-slate-500">{money(difference)} above median</span>;
}

function IncompletePrice({ parts }) {
  const known = parts.reduce((sum, part) => sum + (part.amount ?? 0), 0);
  return (
    <>
      <div className="text-[13px] font-semibold leading-6 text-slate-500 sm:text-sm">Incomplete</div>
      <div className="mt-0.5 hidden text-[13px] tabular-nums text-slate-500 sm:block">{money(known)} known so far</div>
    </>
  );
}

const listed = (names) => new Intl.ListFormat('en').format(names);

function Breakdown({ parts, total }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-slate-900">
        {total === null ? 'What we know so far' : `What makes up ${money(total)}`}
      </h3>
      <dl className="mt-1.5 text-sm leading-6 text-slate-600">
        {parts.map((part) => (
          <div key={part.label} className="flex justify-between gap-4">
            <dt>{part.label}</dt>
            {part.amount === null
              ? <dd className="font-medium text-amber-800">Not on file</dd>
              : <dd className="tabular-nums">{money(part.amount)}</dd>}
          </div>
        ))}
        <div className="mt-1 flex justify-between gap-4 border-t border-slate-300 pt-1 font-semibold text-slate-900">
          <dt>Most you&rsquo;d pay</dt>
          <dd className="tabular-nums">{total === null ? 'Not known' : money(total)}</dd>
        </div>
      </dl>
    </div>
  );
}

// What the member's plan charges of the rate, which is what they'd owe the place.
function YourShare({ plan, costSharing }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-slate-900">Your share</h3>
      <p className="mt-1 text-pretty text-sm leading-5 text-slate-600">{shareText(costSharing, plan.deductible)}</p>
    </div>
  );
}

// Medicare's hospital rate is only a guide here, so it never joins the amounts above.
function MissingBillNote({ location }) {
  return (
    <div className="space-y-1.5 text-pretty text-[13px] leading-5 text-slate-600">
      <p>{location.missing.explain}</p>
      {location.facilityEstimate !== null && (
        <p>As a rough guide, Medicare pays hospitals about {money(location.facilityEstimate)} for this scan. That isn&rsquo;t your plan&rsquo;s rate, so it isn&rsquo;t counted here.</p>
      )}
    </div>
  );
}

function SeveralRatesNote() {
  return (
    <p className="text-pretty text-[13px] leading-5 text-slate-600">
      Your plan lists more than one rate that could apply here without saying which, so this is the highest of them. Ask which you&rsquo;d get.
    </p>
  );
}

// A hospital's own name says nothing about who reads the scan there, so its row names
// the groups that bill for it. At any other place they're the place itself.
function BilledHere({ billers }) {
  return (
    <p className="text-pretty text-[13px] leading-5 text-slate-600">
      {listed(billers)} {billers.length === 1 ? 'bills' : 'bill'} for the reading here.
    </p>
  );
}

const score = (value) => (value === null ? 'No score' : `Score ${Math.round(value)}`);

function ProvidersHere({ providers }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? providers : providers.slice(0, PROVIDERS_SHOWN);
  return (
    <div className="border-t border-emerald-900/10 pt-3">
      <h3 className="text-[13px] font-semibold text-slate-900">Who does this scan here</h3>
      <ul className="mt-1.5 space-y-1.5">
        {shown.map((provider) => (
          <li key={provider.npi} className="flex items-baseline justify-between gap-3 text-sm leading-5">
            <div className="min-w-0">
              <div className="truncate text-slate-900">{provider.name}</div>
              <div className="truncate text-[13px] text-slate-500">{provider.specialty}</div>
            </div>
            <div className="shrink-0 text-[13px] tabular-nums text-slate-600">{score(provider.score)}</div>
          </li>
        ))}
      </ul>
      {providers.length > shown.length && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mt-2 rounded text-[13px] font-medium text-emerald-800 hover:text-emerald-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
        >
          Show all {providers.length}
        </button>
      )}
      <p className="mt-2 text-pretty text-[13px] leading-5 text-slate-500">
        These are the clinicians Medicare shows billing this scan here. Score is Medicare&rsquo;s clinician quality score, out of 100. It isn&rsquo;t a patient review.
      </p>
    </div>
  );
}

function LocationRow({ location, plan, costSharing, selected, highlighted, layoutKey, onSelect, onHover }) {
  const rowRef = useRef(null);

  // Picking a pin on the map brings its row into view.
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [selected]);

  const directions = `https://www.google.com/maps/dir/?api=1&destination=${location.lat},${location.lon}`;

  return (
    <>
      <motion.tr
        ref={rowRef}
        layout="position"
        layoutDependency={layoutKey}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        onClick={() => onSelect(location.id)}
        onMouseEnter={() => onHover(location.id)}
        onMouseLeave={() => onHover(null)}
        className={clsx(
          'cursor-pointer scroll-mt-10 border-b border-slate-100 transition-colors',
          selected ? 'bg-emerald-50/70' : highlighted ? 'bg-slate-50' : 'bg-white',
        )}
      >
        <td className="py-3.5 pl-4 pr-2 align-top sm:pl-6">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <button
              type="button"
              aria-expanded={selected}
              className="rounded text-left text-[15px] font-semibold text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
            >
              {location.name}
            </button>
            {location.isCheapest && <Badge tone="cheapest">Cheapest</Badge>}
            {location.isClosest && <Badge tone="closest">Closest</Badge>}
          </div>
          {/* The street, since one practice can have several sites under one name. */}
          <div className="mt-0.5 text-[13px] text-slate-500">
            {location.kindLabel}, <span className="inline-block">{location.address}</span>
          </div>
          {location.matchedProvider && (
            <div className="mt-0.5 text-[13px] font-medium text-emerald-800">{location.matchedProvider} works here</div>
          )}
          {location.missing && (
            <div className="mt-0.5 text-[13px] font-medium text-amber-800">{location.missing.rowNote}</div>
          )}
        </td>
        <td className="whitespace-nowrap px-2 py-3.5 text-right align-top text-[15px] tabular-nums text-slate-700 sm:px-4">
          {miles(location.distanceMi)}
        </td>
        <td className="whitespace-nowrap py-3.5 pl-2 pr-4 text-right align-top sm:pr-6">
          {location.maxRate === null ? <IncompletePrice parts={location.parts} /> : (
            <>
              <div className="text-[17px] font-semibold leading-6 tabular-nums text-slate-900">{money(location.maxRate)}</div>
              <div className="mt-0.5 hidden text-[13px] tabular-nums sm:block">
                <PriceComparison difference={location.maxVsMedian} />
              </div>
            </>
          )}
        </td>
      </motion.tr>
      <AnimatePresence initial={false}>
        {selected && (
          <motion.tr key="detail" exit={{ opacity: 0 }} className="border-b border-slate-100 bg-emerald-50/70">
            <td colSpan={3} className="p-0">
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="overflow-hidden"
              >
                <div className="px-4 pb-4 sm:px-6">
                  <div className="grid gap-4 sm:grid-cols-2 sm:gap-8">
                    <div className="space-y-4">
                      <Breakdown parts={location.parts} total={location.maxRate} />
                      <YourShare plan={plan} costSharing={costSharing} />
                      {location.missing && <MissingBillNote location={location} />}
                      {location.severalRates && <SeveralRatesNote />}
                      {location.kind === 'hospital' && <BilledHere billers={location.billers} />}
                    </div>
                    <div className="flex flex-col gap-3">
                      <div className="text-sm leading-6 text-slate-600">
                        <div>{location.address}, {location.city}</div>
                        {location.stars !== null && <div>Medicare hospital rating: {location.stars} of 5 stars</div>}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <a
                          href={directions}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(event) => event.stopPropagation()}
                          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white transition-colors hover:bg-slate-700"
                        >
                          <Navigation size={14} aria-hidden="true" /> Directions
                        </a>
                        {location.phone && (
                          <a
                            href={`tel:${location.phone.replace(/\D/g, '')}`}
                            onClick={(event) => event.stopPropagation()}
                            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium tabular-nums text-slate-800 transition-colors hover:bg-slate-50"
                          >
                            <Phone size={14} aria-hidden="true" /> {location.phone}
                          </a>
                        )}
                      </div>
                      {location.providers.length > 0 && <ProvidersHere providers={location.providers} />}
                    </div>
                  </div>
                </div>
              </motion.div>
            </td>
          </motion.tr>
        )}
      </AnimatePresence>
    </>
  );
}

export function ResultsTable({ results, plan, costSharing, sort, layoutKey, selectedId, highlightId, onSort, onSelect, onHover }) {
  return (
    <table className="w-full table-fixed border-separate border-spacing-0 text-left">
      {/* Fixed widths, so opening a row or narrowing the list can't move the columns. */}
      <colgroup>
        <col />
        <col className="w-[88px] sm:w-28" />
        <col className="w-[100px] sm:w-44" />
      </colgroup>
      <thead className="sticky top-0 z-10 bg-white text-[13px] text-slate-500 shadow-[inset_0_-1px_0_var(--color-slate-200)]">
        <tr>
          <th scope="col" className="py-2.5 pl-4 pr-2 font-medium sm:pl-6">Place</th>
          <SortHeader label="Distance" sortKey="distance" sort={sort} onSort={onSort} className="px-2 sm:px-4" />
          <SortHeader label="Most you’d pay" shortLabel="Max" sortKey="price" sort={sort} onSort={onSort} className="pl-2 pr-4 sm:pr-6" />
        </tr>
      </thead>
      <tbody>
        {results.map((location) => (
          <LocationRow
            key={location.id}
            location={location}
            plan={plan}
            costSharing={costSharing}
            selected={location.id === selectedId}
            highlighted={location.id === highlightId}
            layoutKey={layoutKey}
            onSelect={onSelect}
            onHover={onHover}
          />
        ))}
      </tbody>
    </table>
  );
}
