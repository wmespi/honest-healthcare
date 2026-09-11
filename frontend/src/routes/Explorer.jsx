import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { shortNetwork } from '../lib/networkLabels';
import { ExplorerHeader } from '../components/ExplorerHeader';
import { ProcedureSearchBar } from '../components/ProcedureSearchBar';
import { RateOverview } from '../components/RateOverview';
import { SpecialtyDropdown } from '../components/SpecialtyDropdown';
import { NpiSearch } from '../components/NpiSearch';
import { CategoryBrowser } from '../components/CategoryBrowser';
import { PickPlanPrompt } from '../components/PickPlanPrompt';
import { ProviderRateTable } from '../components/ProviderRateTable';
import { NetworkCompare } from '../components/NetworkCompare';
import { ProviderCostCard } from '../components/ProviderCostCard';
import { ProviderMenu } from '../components/ProviderMenu';
import { PlanPanel } from '../components/PlanPanel';
import { TrustBar } from '../components/TrustBar';
import { PlanGate } from '../components/PlanGate';
import { SpecialtyProviderList } from '../components/SpecialtyProviderList';
import { SpecialtyBrowser } from '../components/SpecialtyBrowser';
import { useExplorerState } from './useExplorerState';

// Display labels for `service_line` values (#83) — keep in sync with
// serving/service_lines.py's SERVICE_LINES keys.
const SERVICE_LINE_LABELS = { pcp: 'Primary Care (PCP)' };

// Copy for a service line's "first procedure" scope (the new-patient-visit
// family for PCP). The billing-code allowlist itself comes from GET
// /service_lines (serving/service_lines.py) — see useExplorerState's
// serviceLineCodes, not a local hand-synced copy (#100).
const SERVICE_LINE_CODE_LABELS = { pcp: 'new patient visit' };

// The rate explorer itself — one component, mounted at either /find-care/pcp
// (locked to a service line, #83/#87) or /explore (the general flow). Not
// rendered directly; see the routed `App` default export in App.jsx. All state
// and data-fetching lives in useExplorerState — this file is presentation only.
export function Explorer({ lockedServiceLine }) {
  const s = useExplorerState(lockedServiceLine);
  const { serviceLine, selectedCode, npi, specialty } = s;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 w-full font-sans selection:bg-indigo-500/30 overflow-x-hidden">
      <ExplorerHeader lockedServiceLine={lockedServiceLine} />

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        {lockedServiceLine && (
          <Link to="/" className="inline-block mb-6 text-xs text-slate-500 hover:text-slate-300 transition-colors">
            ← All tasks
          </Link>
        )}

        <TrustBar selectedNetwork={s.selectedPlan} />

        {s.gated && (
          <PlanGate
            selectedPlan={s.selectedPlan}
            onSelect={s.handlePlanSelect}
            onBrowseAll={() => s.setBypassGate(true)}
            heading={lockedServiceLine === 'pcp' ? 'Which plan are you on?' : undefined}
            body={lockedServiceLine === 'pcp'
              ? "So we can show real in-network rates for these providers — pick your plan, or see who's out there first and add it later."
              : undefined}
            browseLabel={lockedServiceLine === 'pcp' ? 'See PCPs without picking a plan yet' : undefined}
          />
        )}

        {!s.gated && <>
        {lockedServiceLine && (
          <p className="mb-8 text-xs text-slate-500">
            {s.selectedPlan
              ? <>On <span className="text-slate-300 font-semibold">{shortNetwork(s.selectedPlan)}</span>.{' '}
                  <button onClick={() => { s.setSelectedPlan(''); s.setBypassGate(false); }} className="underline underline-offset-2 hover:text-slate-300">
                    change plan
                  </button></>
              : <>Browsing without a plan — prices below aren't real yet.{' '}
                  <button onClick={() => s.setBypassGate(false)} className="underline underline-offset-2 hover:text-slate-300">
                    add your plan
                  </button></>
            }
          </p>
        )}
        {/* The network dropdown + free-text procedure search only make sense
            on the general explorer — a locked service-line route already
            knows what you're looking for. */}
        {!lockedServiceLine && (
          <ProcedureSearchBar
            selectedPlan={s.selectedPlan}
            onPlanSelect={s.handlePlanSelect}
            query={s.query}
            onQueryChange={s.setQuery}
            onFocus={() => s.setIsFocused(true)}
            onBlur={() => setTimeout(() => { s.setIsFocused(false); s.setShowSuggestions(false); }, 200)}
            showSuggestions={s.showSuggestions}
            suggestions={s.suggestions}
            onSuggestionClick={s.handleSuggestionClick}
          />
        )}

        <PlanPanel
          form={s.planParams.form}
          setField={s.planParams.setField}
          setCopay={s.planParams.setCopay}
          clear={s.planParams.clear}
          configured={s.planConfigured}
        />

        {/* Browsing by category is a way to *find* a procedure — redundant
            once a service line already fixes what you're looking for. */}
        {!lockedServiceLine && <CategoryBrowser onPick={s.handleCategoryPick} />}

        {/* "Pick your care" — the specialty step. Shown until a specialty,
            service line, provider, or procedure narrows the view. */}
        {!specialty && !serviceLine && !npi && !selectedCode?.code && (
          <SpecialtyBrowser onSelect={s.handleSpecialtyChange} network={s.selectedPlan} />
        )}

        {/* Filters row */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-8">
          {/* Service-line scope (#83) — fixed by the route (#87), not a
              picker: an exact taxonomy-code allowlist for whichever task the
              user is on. "Leaving" it means navigating away to the general
              explorer, not clearing a filter. Mutually exclusive with the
              free-text specialty filter below (never both truthy — only
              /explore has specialty, only a locked route has serviceLine). */}
          {serviceLine && (
            <div className="flex items-center gap-2 bg-slate-900 border border-indigo-500/50 rounded-xl px-3 h-8">
              <span className="text-xs text-indigo-300 font-bold">
                {SERVICE_LINE_LABELS[serviceLine] || serviceLine}
              </span>
              <button
                onClick={() => s.navigate('/explore')}
                className="text-slate-500 hover:text-white shrink-0"
                title="Browse all networks and procedures instead"
              >
                <X size={12} />
              </button>
            </div>
          )}

          {/* Specialty scope (a filter) — the SpecialtyBrowser above is the
              picker on the empty landing; here it's the change / clear control.
              Hidden while a service line is active — the two shouldn't collide. */}
          {!serviceLine && (specialty || npi || selectedCode?.code) && (
            <>
              <SpecialtyDropdown selected={specialty} onSelect={s.handleSpecialtyChange} network={s.selectedPlan} />
              <div className="hidden sm:block w-px h-5 bg-slate-800" />
            </>
          )}

          {/* One specific provider (a drill-down) */}
          <NpiSearch selectedNpi={npi} onSelect={s.handleNpiSelect} network={s.selectedPlan} label={s.npiLabel} />
        </div>

        {/* Loading */}
        {s.loading && (
          <div className="py-20 flex flex-col items-center justify-center">
            <div className="w-14 h-14 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mb-5" />
            <div className="text-indigo-400 font-bold tracking-widest uppercase text-[10px] animate-pulse">
              Querying MRF data...
            </div>
          </div>
        )}

        {/* Error */}
        {s.error && !s.loading && (
          <div className="py-10 text-center text-rose-400 font-medium">{s.error}</div>
        )}

        {/* Specialty or service line chosen, no provider or procedure yet →
            the ranked provider list */}
        {(specialty || serviceLine) && !npi && !selectedCode?.code && !s.loading && (
          <SpecialtyProviderList
            specialty={specialty}
            label={serviceLine ? (SERVICE_LINE_LABELS[serviceLine] || serviceLine) : undefined}
            providers={s.specialtyProviders}
            loading={s.specialtyProvidersLoading}
            onPick={s.handleNpiSelect}
          />
        )}

        {/* Provider menu — provider chosen, no procedure yet */}
        {npi && !selectedCode?.code && !s.loading && (
          <ProviderMenu
            data={s.providerMenu}
            loading={s.providerMenuLoading}
            onPick={s.handleMenuPick}
            providerName={s.npiLabel}
            network={s.selectedPlan}
            onClearNetwork={() => { s.setBypassGate(true); s.handlePlanSelect(''); }}
            onShowAll={() => s.setMenuTier('all')}
            plan={s.planParams.plan}
            codeFilter={s.activeCodeScope}
            codeFilterLabel={serviceLine ? SERVICE_LINE_CODE_LABELS[serviceLine] : undefined}
          />
        )}

        {/* Results */}
        <AnimatePresence>
          {s.distribution && !s.loading && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
              {/* The overview stack (disclaimer / stat grid / meta / histogram) is
                  the "how is this priced in general" view — useful when
                  browsing or comparing providers, noise once a specific
                  provider + procedure is chosen (#73): that view gets the
                  compact RateSitsStrip inside the cost card instead. */}
              {!(selectedCode?.code && npi) && (
                <RateOverview
                  summary={s.summary}
                  specialty={specialty}
                  selectedCode={selectedCode}
                  buckets={s.buckets}
                  medianBucket={s.medianBucket}
                />
              )}

              {/* Job 2 — the same procedure across every network */}
              {selectedCode?.code && (
                <NetworkCompare
                  data={s.networkCompare}
                  loading={s.networkCompareLoading}
                  expanded={s.networkCompareExpanded}
                  onExpand={() => s.setNetworkCompareExpanded(true)}
                  selectedNetwork={s.selectedPlan}
                  onPickNetwork={s.handlePlanSelect}
                  plan={s.planParams.plan}
                  rbcsCategory={selectedCode?.rbcs_category}
                />
              )}

              {/* Provider + procedure both chosen → the cost answer (job 1).
                  Procedure only → the compare-across-providers table (job 3).
                  Both are plan-specific — prompt for a plan first. */}
              {selectedCode?.code && npi && (
                s.selectedPlan
                  ? <ProviderCostCard
                      data={s.providerQuote}
                      loading={s.providerQuoteLoading}
                      providerName={s.npiLabel}
                      plan={s.planParams.plan}
                      rbcsCategory={selectedCode?.rbcs_category}
                    />
                  : <PickPlanPrompt what="see the rate at this provider" />
              )}
              {selectedCode?.code && !npi && (
                s.selectedPlan
                  ? <ProviderRateTable data={s.providerRates} loading={s.providerRatesLoading} specialty={specialty} />
                  : <PickPlanPrompt what="compare providers for this procedure" />
              )}
            </motion.div>
          )}
        </AnimatePresence>
        </>}

      </main>
    </div>
  );
}
