// The one source of explanatory copy. Components import from here so a number
// is never explained two different ways on two screens.

export const IMAGING_EXPLAIN = {
  number:
    'The price is what Anthem has negotiated with this practice for the scan, before your benefits apply. It is not what you will pay.',
  pay:
    'What you owe depends on your plan: you pay the full negotiated price until your deductible is met, then your copay or coinsurance, up to your out-of-pocket maximum.',
  source:
    "Anthem's public price-transparency files for the plan you picked, matched to practices by their billing ID.",
  noShare:
    'Pick your plan version above to see what you would pay. Without it this page shows prices only, not your share.',
  facility: (amount) => ({
    title: 'If a hospital does the scan',
    body: `Medicare pays a hospital about ${amount} for the facility part of this scan. Treat it as a rough guide: it is Medicare\u2019s national rate, not a price Anthem has published for any hospital, and what your plan pays a hospital can differ. An imaging center’s all-in price already includes it.`,
  }),
  limits: [
    'Hospital and facility fees are not included in the prices shown. When a hospital performs the scan, it bills its own fee separately, and some prices cover just the doctor who reads the images.',
    'A practice can hold several contracts. The typical price is the middle one; the range shows the cheapest and the dearest.',
    'Out-of-network care is not priced. Confirm the practice is in your network before you book.',
  ],
};

// How a row's price was formed, in words a patient can act on.
export function basisNote(basis) {
  switch (basis) {
    case 'global':
      return 'Complete price: equipment and reading together.';
    case 'split':
      return 'Equipment and reading are billed as two parts; the price adds them.';
    case 'professional_only':
      return 'Reading only. The facility fee for the scan itself is not included.';
    case 'technical_only':
      return 'Equipment only. The radiologist’s reading is billed separately.';
    default:
      return '';
  }
}

export const COST_SHARE_EXPLAIN = {
  source: 'Deductible, out-of-pocket maximum and coinsurance are copied from the plan\u2019s Summary of Benefits and Coverage.',
  preventive: 'A routine screening mammogram is a preventive service: most plans cover it with no cost to you. A mammogram to look into a problem is billed as diagnostic.',
  unmodelled: (text) => `Your plan lists this scan as \u201c${text}\u201d, which we cannot turn into a dollar amount yet.`,
  assumption: (met) => (met > 0
    ? 'Assumes the deductible you have met so far, and no other care this year.'
    : 'Assumes none of your deductible is met yet, and no other care this year.'),
};
