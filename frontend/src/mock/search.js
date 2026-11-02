import { KIND_LABELS, LOCATIONS, MISSING_BILLS, PLANS, SAMPLE_ADDRESSES, SCAN_PARTS, SERVICES, ZIP_AREAS } from './sampleData';

export const RADIUS_OPTIONS = [5, 10, 25, 50];

const ENOUGH_CHOICES = 5;
const HOSPITAL_FEE_SHARE = 0.45;
const MEDICARE_SHARE_OF_BASE = 0.4;
const MEDICARE_FACILITY_SHARE_OF_BASE = 0.5;
const [, READING] = SCAN_PARTS;
const EARTH_RADIUS_MI = 3958.8;
const toRadians = (degrees) => (degrees * Math.PI) / 180;

export function distanceMiles(a, b) {
  const dLat = toRadians(b.lat - a.lat);
  const dLon = toRadians(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(toRadians(a.lat)) * Math.cos(toRadians(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_MI * Math.asin(Math.sqrt(h));
}

const normalize = (text) => text.trim().toLowerCase();
const roundedMiles = (origin, place) => Math.round(distanceMiles(origin, place) * 10) / 10;

// Stands in for a geocoder: a known ZIP anywhere in the text, or a place name.
export function resolveLocation(text) {
  const query = normalize(text ?? '');
  if (!query) return null;
  const zip = query.match(/\b\d{5}\b/)?.[0];
  const place = zip
    ? ZIP_AREAS.find((p) => p.zip === zip)
    : ZIP_AREAS.find((p) => normalize(p.name) === query);
  return place ? { lat: place.lat, lon: place.lon, placeName: place.name } : null;
}

export function suggestLocations(text) {
  const query = normalize(text ?? '');
  const places = ZIP_AREAS.map((p) => ({ id: p.zip, label: p.zip, hint: p.name }));
  const addresses = SAMPLE_ADDRESSES.map((a) => ({ id: a, label: a }));
  if (!query) return places.slice(0, 6);
  const options = [...places, ...addresses];
  // A finished entry needs no list, which would only cover what comes next.
  if (options.some((o) => normalize(o.label) === query)) return [];
  return options
    .filter((o) => normalize(`${o.label} ${o.hint ?? ''}`).includes(query))
    .slice(0, 6);
}

export function suggestServices(text) {
  const query = normalize(text ?? '');
  return SERVICES
    .filter((s) => normalize(`${s.name} ${s.keywords}`).includes(query))
    .map((s) => ({ id: s.id, label: s.name }));
}

export function suggestPlans(text) {
  const query = normalize(text ?? '');
  return PLANS
    .filter((p) => normalize(`${p.name} ${p.carrier}`).includes(query))
    .map((p) => ({ id: p.id, label: p.name, hint: p.carrier }));
}

// Opens on the tightest area with real choices, so a dense neighborhood stays local
// and a suburb still gets an answer.
export function defaultRadius({ serviceId, origin }) {
  const service = SERVICES.find((s) => s.id === serviceId);
  const distances = LOCATIONS
    .filter((p) => service.kinds.includes(p.kind))
    .map((p) => roundedMiles(origin, p));
  return RADIUS_OPTIONS.find((radius) => distances.filter((d) => d <= radius).length >= ENOUGH_CHOICES)
    ?? RADIUS_OPTIONS[RADIUS_OPTIONS.length - 1];
}

// A small, stable wobble so two places at the same priceLevel don't tie.
function wobble(key) {
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) % 1000;
  return 0.94 + (hash / 1000) * 0.12;
}

function median(sorted) {
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

// The separate charges behind a full price. They always add up to it exactly.
function priceParts(price) {
  let remaining = price;
  return SCAN_PARTS.map((part, index) => {
    const amount = index === SCAN_PARTS.length - 1 ? remaining : Math.round(price * part.share);
    remaining -= amount;
    return { label: part.label, amount };
  });
}

// A place's rates for one scan. With a bill missing there is no ceiling to claim, only the
// reading, which is the part known, and the bill named as missing.
function ratesAt(place, scaled) {
  const missing = MISSING_BILLS[place.kind] ?? null;
  if (missing) {
    const readingShare = READING.share * (place.kind === 'hospital' ? 1 - HOSPITAL_FEE_SHARE : 1);
    return {
      missing,
      minRate: null,
      medianRate: null,
      maxRate: null,
      parts: [{ label: READING.label, amount: Math.round(scaled * readingShare) }, { label: missing.label, amount: null }],
    };
  }
  const maxRate = Math.max(1, Math.round(scaled));
  const minRate = Math.round(maxRate * (1 - place.spread));
  return { missing, minRate, medianRate: Math.round((minRate + maxRate) / 2), maxRate, parts: priceParts(maxRate) };
}

// A place with no full price has no ceiling to compare, so it sorts after every place that does.
const ceiling = (p) => p.maxRate ?? Infinity;
const SORTS = {
  price: (a, b) => ceiling(a) - ceiling(b) || a.distanceMi - b.distanceMi,
  distance: (a, b) => a.distanceMi - b.distanceMi || ceiling(a) - ceiling(b),
};

// The response this mock wants from a future API: one row per place for one scan and
// plan, with the lowest, median and highest rate on file there. The most someone could
// pay is the highest, since the file doesn't say which rate applies to them. A place missing
// a bill has no highest rate, and doesn't count toward the cheapest or the median.
export function findCare({ serviceId, planId, origin, radiusMi, sort = 'price' }) {
  const service = SERVICES.find((s) => s.id === serviceId);
  const plan = PLANS.find((p) => p.id === planId);
  const medicareRate = Math.round(service.basePrice * MEDICARE_SHARE_OF_BASE);
  const medicareFacilityRate = Math.round(service.basePrice * MEDICARE_FACILITY_SHARE_OF_BASE);

  const offering = LOCATIONS
    .filter((p) => service.kinds.includes(p.kind))
    .map((p) => {
      // Keyed by name, so every office of one practice shares a price.
      const scaled = service.basePrice * p.priceLevel * plan.priceFactor * wobble(p.name + service.id + plan.id);
      return {
        id: p.id,
        name: p.name,
        kindLabel: KIND_LABELS[p.kind],
        neighborhood: p.neighborhood,
        address: p.address,
        phone: p.phone,
        hours: p.hours,
        lat: p.lat,
        lon: p.lon,
        stars: p.stars,
        billers: p.billers,
        doctors: p.doctors,
        ...ratesAt(p, scaled),
        facilityEstimate: p.kind === 'hospital' ? medicareFacilityRate : null,
        distanceMi: roundedMiles(origin, p),
      };
    });

  const nearby = offering.filter((p) => p.distanceMi <= radiusMi);
  if (nearby.length === 0) {
    const closest = [...offering].sort(SORTS.distance)[0];
    const widerRadiusMi = RADIUS_OPTIONS.find((r) => closest.distanceMi <= r) ?? null;
    return { results: [], cheapest: null, nearbyMedian: null, pricedCount: 0, medicareRate, closestOutside: closest, widerRadiusMi };
  }

  const priced = nearby.filter((p) => p.maxRate !== null);
  const nearbyMedian = priced.length > 0 ? median(priced.map((p) => p.maxRate).sort((a, b) => a - b)) : null;
  const cheapest = [...priced].sort(SORTS.price)[0] ?? null;
  const closest = [...nearby].sort(SORTS.distance)[0];
  const results = nearby
    .map((p) => ({
      ...p,
      maxVsMedian: p.maxRate === null ? null : p.maxRate - nearbyMedian,
      isCheapest: p.id === cheapest?.id,
      isClosest: p.id === closest.id,
    }))
    .sort(SORTS[sort]);

  // With no full price nearby, the useful next step is out to the closest one.
  const closestPriced = cheapest ? null : offering.filter((p) => p.maxRate !== null).sort(SORTS.distance)[0];
  const widerRadiusMi = closestPriced ? RADIUS_OPTIONS.find((r) => closestPriced.distanceMi <= r) ?? null : null;

  return { results, cheapest, nearbyMedian, pricedCount: priced.length, medicareRate, closestOutside: null, widerRadiusMi };
}

// Narrows the list to a place or doctor by name. A doctor match moves to the top of that
// place's doctors, so someone with a referral sees them first.
export function filterResults(results, text) {
  const query = normalize(text ?? '');
  if (!query) return results;
  return results.flatMap((result) => {
    if (normalize(result.name).includes(query)) return [result];
    const doctor = result.doctors.find((d) => normalize(d.name).includes(query));
    if (!doctor) return [];
    return [{ ...result, matchedDoctor: doctor.name, doctors: [doctor, ...result.doctors.filter((d) => d !== doctor)] }];
  });
}
