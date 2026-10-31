import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { clsx } from 'clsx';
import { ChevronDown } from 'lucide-react';

function NoteList({ title, items }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-slate-900">{title}</h3>
      <ul className="mt-1.5 list-disc space-y-1 pl-4 text-sm leading-5 text-slate-600 marker:text-slate-300">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

// What a person should know about the service itself, whichever provider they pick.
export function AboutService({ serviceName, details, plan }) {
  const [open, setOpen] = useState(false);
  const referral = plan.needsReferral && !details.noReferral
    ? [`${plan.name} is an HMO, so you’ll likely need a referral from your primary care doctor.`]
    : [];

  return (
    <div className="mx-4 mb-4 rounded-xl border border-slate-200 sm:mx-6">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left text-sm font-medium text-slate-900 transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
      >
        <span>What to know about: {serviceName}</span>
        <ChevronDown size={16} aria-hidden="true" className={clsx('shrink-0 text-slate-400 transition-transform', open && 'rotate-180')} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <div className="space-y-4 px-4 pb-4">
              <p className="text-sm leading-5 text-slate-600">{details.about}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <NoteList title="What could change the most you’d pay" items={details.couldAdd} />
                <NoteList title="Before you book" items={[...referral, ...details.asks]} />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
