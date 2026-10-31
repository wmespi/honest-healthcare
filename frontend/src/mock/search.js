import { KIND_LABELS, PLACES, PROVIDERS, SAMPLE_ADDRESSES, SERVICES } from './sampleData';

export const RADIUS_OPTIONS = [5, 10, 25, 50];

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

const SORTS = {
  price: (a, b) => a.price - b.price || a.distanceMi - b.distanceMi,
  distance: (a, b) => a.distanceMi - b.distanceMi || a.price - b.price,
};

// The response this mock wants from a future API: one row per provider for one
// service near one place, with the comparisons the screen shows already worked out.
export function findCare({ serviceId, origin, radiusMi, sort = 'price' }) {
  const service = SERVICES.find((s) => s.id === serviceId);
  const offering = PROVIDERS
    .filter((p) => service.kinds.includes(p.kind))
    .map((p) => ({
      id: p.id,
      name: p.name,
      kindLabel: KIND_LABELS[p.kind],
      neighborhood: p.neighborhood,
      address: p.address,
      phone: p.phone,
      hours: p.hours,
      lat: p.lat,
      lon: p.lon,
      price: Math.max(1, Math.round(service.basePrice * p.priceLevel * wobble(p.id + service.id))),
      distanceMi: Math.round(distanceMiles(origin, p) * 10) / 10,
    }));

  const nearby = offering.filter((p) => p.distanceMi <= radiusMi);
  if (nearby.length === 0) {
    const closest = [...offering].sort(SORTS.distance)[0];
    const widerRadiusMi = RADIUS_OPTIONS.find((r) => closest.distanceMi <= r) ?? null;
    return { results: [], cheapest: null, typicalPrice: null, closestOutside: closest, widerRadiusMi };
  }

  const typicalPrice = median(nearby.map((p) => p.price).sort((a, b) => a - b));
  const cheapest = [...nearby].sort(SORTS.price)[0];
  const closest = [...nearby].sort(SORTS.distance)[0];
  const results = nearby
    .map((p) => ({
      ...p,
      priceVsTypical: p.price - typicalPrice,
      isCheapest: p.id === cheapest.id,
      isClosest: p.id === closest.id,
    }))
    .sort(SORTS[sort]);

  return { results, cheapest, typicalPrice, closestOutside: null, widerRadiusMi: null };
}
