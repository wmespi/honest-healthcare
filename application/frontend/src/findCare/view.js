// What the screen shows for a /find-care answer (application/serving/contract.py), worked out
// once here so the components only lay it out.
import { displayName, money } from './format';

const round2 = (amount) => Math.round(amount * 100) / 100;

export function placeView(place, { cheapestId, closestId, median, facilityRate }) {
  // A place with a part the plan doesn't price has no "most you'd pay", only what's known so far.
  const missingPart = place.parts.find((part) => part.max === null);
  const facility = place.parts.find((part) => part.billing_class === 'institutional');
  return {
    id: place.place_id,
    name: displayName(place.name),
    kind: place.kind,
    kindLabel: place.kind_name,
    city: displayName(place.city),
    address: displayName(place.address),
    phone: place.phone,
    stars: place.stars,
    lat: place.lat,
    lon: place.lon,
    distanceMi: place.distance_mi,
    maxRate: place.max_rate,
    parts: place.parts.map((part) => ({ label: part.label, amount: part.max })),
    missing: missingPart ? { rowNote: missingPart.missing_note, explain: missingPart.missing_explain } : null,
    severalRates: place.several_rates,
    billers: place.billers.map(displayName),
    providers: place.providers.map((p) => ({ ...p, name: displayName(p.name) })),
    facilityEstimate: place.kind === 'hospital' && facility?.max === null ? facilityRate : null,
    cashPrice: place.cash_price ?? null,
    feeFromHospital: facility?.source === 'hospital',
    isCheapest: place.place_id === cheapestId,
    isClosest: place.place_id === closestId,
    maxVsMedian: place.max_rate === null || median === null ? null : round2(place.max_rate - median),
  };
}

// The search as the screen shows it: every result, the cheapest and the closest
// marked, and the nearest place outside the radius when there are none.
export function searchView(body, service) {
  const context = {
    cheapestId: body.cheapest_place_id,
    closestId: body.closest_place_id,
    median: body.median_max_rate,
    facilityRate: service?.medicare_facility_rate ?? null,
  };
  const results = body.results.map((place) => placeView(place, context));
  return {
    results,
    cheapest: results.find((p) => p.isCheapest) ?? null,
    nearbyMedian: body.median_max_rate,
    pricedCount: results.filter((p) => p.maxRate !== null).length,
    medicareRate: service?.medicare_rate ?? null,
    closestOutside: body.closest_outside ? placeView(body.closest_outside, context) : null,
    widerRadiusMi: body.wider_radius_mi,
    costSharing: body.cost_sharing,
  };
}

// One share per benefit, each led by its label when there are several.
export function shareLines(costSharing, deductible) {
  const shares = Array.isArray(costSharing) ? costSharing : [costSharing];
  return shares.map((share) => ({
    key: share.benefit,
    label: shares.length > 1 ? share.label : null,
    text: shareText(share, deductible),
  }));
}

// What the member pays of a place's rate on their plan, as one sentence.
export function shareText({ copay, coinsurance, after_deductible: afterDeductible }, deductible) {
  let share;
  if (copay && coinsurance) share = `a ${money(copay)} copay and then ${coinsurance}% of the rest`;
  else if (copay) share = `a ${money(copay)} copay`;
  else if (coinsurance) share = `${coinsurance}% of the rate`;
  else share = 'nothing';
  if (!afterDeductible) return `You pay ${share}.`;
  return `You pay the whole rate until you’ve met your ${money(deductible)} deductible. After that, you pay ${share}.`;
}

const normalize = (text) => text.trim().toLowerCase();

// Narrows the list to a place or provider by name. A provider match moves to the top of
// that place's providers, so someone with a referral sees them first.
export function filterResults(results, text) {
  const query = normalize(text ?? '');
  if (!query) return results;
  return results.flatMap((result) => {
    if (normalize(result.name).includes(query)) return [result];
    const provider = result.providers.find((p) => normalize(p.name).includes(query));
    if (!provider) return [];
    return [{ ...result, matchedProvider: provider.name, providers: [provider, ...result.providers.filter((p) => p !== provider)] }];
  });
}

// The map's pins from the bottom of the stack to the top: a place with no full price under
// them all, then the dearest up to the cheapest, and among places at one price the cheapest
// result on top, so the pin the screen names is never covered.
export function pinStack(results) {
  return [...results].sort((a, b) => (b.maxRate ?? Infinity) - (a.maxRate ?? Infinity)
    || Number(a.isCheapest) - Number(b.isCheapest));
}
