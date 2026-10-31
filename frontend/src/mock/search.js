import { KIND_LABELS, PLACES, PLANS, PROVIDERS, SAMPLE_ADDRESSES, SERVICES, SERVICE_DETAILS } from './sampleData';

export const RADIUS_OPTIONS = [5, 10, 25, 50];

const ENOUGH_CHOICES = 5;
const HOSPITAL_FEE_SHARE = 0.45;
const MEDICARE_SHARE_OF_BASE = 0.4;
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
const roundedMiles = (origin, provider) => Math.round(distanceMiles(origin, provider) * 10) / 10;

// Stands in for a geocoder: a known ZIP anywhere in the text, or a place name.
export function resolveLocation(text) {
  const query = normalize(text ?? '');
  if (!query) return null;
  const zip = query.match(/\b\d{5}\b/)?.[0];
  const place = zip
    ? PLACES.find((p) => p.zip === zip)
    : PLACES.find((p) => normalize(p.name) === query);
  return place ? { lat: place.lat, lon: place.lon, placeName: place.name } : null;
}

export function suggestLocations(text) {
  const query = normalize(text ?? '');
  const places = PLACES.map((p) => ({ id: p.zip, label: p.zip, hint: p.name }));
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
    .filter((s) => normalize(`${s.name} ${s.group} ${s.keywords}`).includes(query))
    .map((s) => ({ id: s.id, label: s.name, hint: s.group }));
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
  const distances = PROVIDERS
    .filter((p) => service.kinds.includes(p.kind))
    .map((p) => roundedMiles(origin, p));
  return RADIUS_OPTIONS.find((radius) => distances.filter((d) => d <= radius).length >= ENOUGH_CHOICES)
    ?? RADIUS_OPTIONS[RADIUS_OPTIONS.length - 1];
}

// A small, stable wobble so two providers at the same priceLevel don't tie.
function wobble(key) {
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) % 1000;
  return 0.94 + (hash / 1000) * 0.12;
}

function median(sorted) {
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

// The separate charges behind a price. They always add up to the price exactly.
function priceParts(details, kind, price) {
  const parts = kind === 'hospital' && details.hospitalFee
    ? [
      { label: 'Hospital facility fee', share: HOSPITAL_FEE_SHARE },
      ...details.parts.map((p) => ({ ...p, share: p.share * (1 - HOSPITAL_FEE_SHARE) })),
    ]
    : details.parts;
  let remaining = price;
  return parts.map((part, index) => {
    const amount = index === parts.length - 1 ? remaining : Math.round(price * part.share);
    remaining -= amount;
    return { label: part.label, amount };
  });
}

const SORTS = {
  price: (a, b) => a.price - b.price || a.distanceMi - b.distanceMi,
  distance: (a, b) => a.distanceMi - b.distanceMi || a.price - b.price,
};

// The response this mock wants from a future API: one row per provider for one
// service, plan and place, with the comparisons the screen shows already worked out.
export function findCare({ serviceId, planId, origin, radiusMi, sort = 'price' }) {
  const service = SERVICES.find((s) => s.id === serviceId);
  const plan = PLANS.find((p) => p.id === planId);
  const details = SERVICE_DETAILS[serviceId];
  const medicareRate = Math.round(service.basePrice * MEDICARE_SHARE_OF_BASE);

  const offering = PROVIDERS
    .filter((p) => service.kinds.includes(p.kind))
    .map((p) => {
      const scaled = service.basePrice * p.priceLevel * plan.priceFactor * wobble(p.id + service.id + plan.id);
      const price = Math.max(1, Math.round(scaled));
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
        price,
        parts: priceParts(details, p.kind, price),
        distanceMi: roundedMiles(origin, p),
      };
    });

  const nearby = offering.filter((p) => p.distanceMi <= radiusMi);
  if (nearby.length === 0) {
    const closest = [...offering].sort(SORTS.distance)[0];
    const widerRadiusMi = RADIUS_OPTIONS.find((r) => closest.distanceMi <= r) ?? null;
    return { results: [], cheapest: null, medianPrice: null, medicareRate, closestOutside: closest, widerRadiusMi };
  }

  const medianPrice = median(nearby.map((p) => p.price).sort((a, b) => a - b));
  const cheapest = [...nearby].sort(SORTS.price)[0];
  const closest = [...nearby].sort(SORTS.distance)[0];
  const results = nearby
    .map((p) => ({
      ...p,
      priceVsMedian: p.price - medianPrice,
      isCheapest: p.id === cheapest.id,
      isClosest: p.id === closest.id,
    }))
    .sort(SORTS[sort]);

  return { results, cheapest, medianPrice, medicareRate, closestOutside: null, widerRadiusMi: null };
}
