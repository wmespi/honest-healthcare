import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { ArrowDown, Navigation, Phone } from 'lucide-react';
import { miles, money } from './format';

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

function Breakdown({ parts, total }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-slate-900">What makes up {money(total)}</h3>
      <dl className="mt-1.5 text-sm leading-6 text-slate-600">
        {parts.map((part) => (
          <div key={part.label} className="flex justify-between gap-4">
            <dt>{part.label}</dt>
            <dd className="tabular-nums">{money(part.amount)}</dd>
          </div>
        ))}
        <div className="mt-1 flex justify-between gap-4 border-t border-slate-300 pt-1 font-semibold text-slate-900">
          <dt>Most you&rsquo;d pay</dt>
          <dd className="tabular-nums">{money(total)}</dd>
        </div>
      </dl>
    </div>
  );
}

function ProviderRow({ provider, selected, highlighted, layoutKey, onSelect, onHover }) {
  const rowRef = useRef(null);

  // Picking a pin on the map brings its row into view.
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [selected]);

  const directions = `https://www.google.com/maps/dir/?api=1&destination=${provider.lat},${provider.lon}`;

  return (
    <>
      <motion.tr
        ref={rowRef}
        layout="position"
        layoutDependency={layoutKey}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        onClick={() => onSelect(provider.id)}
        onMouseEnter={() => onHover(provider.id)}
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
              {provider.name}
            </button>
            {provider.isCheapest && <Badge tone="cheapest">Cheapest</Badge>}
            {provider.isClosest && <Badge tone="closest">Closest</Badge>}
          </div>
          <div className="mt-0.5 text-[13px] text-slate-500">
            {provider.kindLabel}, {provider.neighborhood}
          </div>
        </td>
        <td className="whitespace-nowrap px-2 py-3.5 text-right align-top text-[15px] tabular-nums text-slate-700 sm:px-4">
          {miles(provider.distanceMi)}
        </td>
        <td className="whitespace-nowrap py-3.5 pl-2 pr-4 text-right align-top sm:pr-6">
          <div className="text-[17px] font-semibold leading-6 tabular-nums text-slate-900">{money(provider.price)}</div>
          <div className="mt-0.5 hidden text-[13px] tabular-nums sm:block">
            <PriceComparison difference={provider.priceVsMedian} />
          </div>
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
                <div className={clsx('grid gap-4 px-4 pb-4 sm:px-6', provider.parts.length > 1 && 'sm:grid-cols-2 sm:gap-8')}>
                  {provider.parts.length > 1 && <Breakdown parts={provider.parts} total={provider.price} />}
                  <div className="flex flex-col gap-3">
                    <div className="text-sm leading-6 text-slate-600">
                      <div>{provider.address}</div>
                      <div>{provider.hours}</div>
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
                    <a
                      href={`tel:${provider.phone.replace(/\D/g, '')}`}
                      onClick={(event) => event.stopPropagation()}
                      className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium tabular-nums text-slate-800 transition-colors hover:bg-slate-50"
                    >
                      <Phone size={14} aria-hidden="true" /> {provider.phone}
                    </a>
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

export function ResultsTable({ results, sort, layoutKey, selectedId, highlightId, onSort, onSelect, onHover }) {
  return (
    <table className="w-full border-separate border-spacing-0 text-left">
      <thead className="sticky top-0 z-10 bg-white text-[13px] text-slate-500 shadow-[inset_0_-1px_0_var(--color-slate-200)]">
        <tr>
          <th scope="col" className="py-2.5 pl-4 pr-2 font-medium sm:pl-6">Provider</th>
          <SortHeader label="Distance" sortKey="distance" sort={sort} onSort={onSort} className="px-2 sm:px-4" />
          <SortHeader label="Most you’d pay" shortLabel="Max" sortKey="price" sort={sort} onSort={onSort} className="pl-2 pr-4 sm:pr-6" />
        </tr>
      </thead>
      <tbody>
        {results.map((provider) => (
          <ProviderRow
            key={provider.id}
            provider={provider}
            selected={provider.id === selectedId}
            highlighted={provider.id === highlightId}
            layoutKey={layoutKey}
            onSelect={onSelect}
            onHover={onHover}
          />
        ))}
      </tbody>
    </table>
  );
}
