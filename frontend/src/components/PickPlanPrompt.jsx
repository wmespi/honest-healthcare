// Shown in place of the provider-compare table / cost card until a plan is
// picked — those views are plan-specific (a practice's rate on an HMO isn't its
// rate on a PPO) and the query needs the network to stay fast.
export function PickPlanPrompt({ what }) {
  return (
    <div className="mt-8 bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center">
      <p className="text-white font-black text-lg tracking-tight">Pick your plan to {what}</p>
      <p className="text-slate-400 text-sm mt-2 max-w-md mx-auto leading-relaxed">
        Negotiated rates are plan-specific — choose your plan in the selector above
        and this fills in.
      </p>
    </div>
  );
}
