import { describe, it, expect } from 'vitest';
import { findCare, resolveLocation, suggestLocations, suggestServices } from './search';

const VIRGINIA_HIGHLAND = resolveLocation('30306');
const DULUTH = resolveLocation('30096');

describe('resolveLocation', () => {
  it('places a ZIP, an address with a ZIP, and a place name', () => {
    expect(VIRGINIA_HIGHLAND.placeName).toBe('Virginia-Highland');
    expect(resolveLocation('145 Clairemont Ave, Decatur, GA 30030').placeName).toBe('Decatur');
    expect(resolveLocation('buckhead').placeName).toBe('Buckhead');
  });

  it('returns null for anything outside the sample', () => {
    expect(resolveLocation('90210')).toBeNull();
    expect(resolveLocation('somewhere else')).toBeNull();
    expect(resolveLocation('')).toBeNull();
    expect(resolveLocation(null)).toBeNull();
  });
});

describe('suggestions', () => {
  it('stop once the entry is finished, so the list does not cover the button', () => {
    expect(suggestLocations('3030').length).toBeGreaterThan(0);
    expect(suggestLocations('30306')).toEqual([]);
  });

  it('find a service by a word that is not in its name', () => {
    expect(suggestServices('lumbar').map((o) => o.id)).toEqual(['mri-lower-back']);
  });
});

describe('findCare', () => {
  const search = (overrides) => findCare({
    serviceId: 'mri-lower-back', origin: VIRGINIA_HIGHLAND, radiusMi: 10, ...overrides,
  });

  it('keeps only providers offering the service inside the radius', () => {
    const { results } = search();
    expect(results.length).toBeGreaterThan(0);
    expect(results.every((r) => r.distanceMi <= 10)).toBe(true);
    expect(results.every((r) => ['Imaging center', 'Hospital outpatient'].includes(r.kindLabel))).toBe(true);
    expect(search({ radiusMi: 25 }).results.length).toBeGreaterThan(results.length);
  });

  it('sorts by price by default and by distance on request', () => {
    const byPrice = search().results.map((r) => r.price);
    expect(byPrice).toEqual([...byPrice].sort((a, b) => a - b));
    const byDistance = search({ sort: 'distance' }).results.map((r) => r.distanceMi);
    expect(byDistance).toEqual([...byDistance].sort((a, b) => a - b));
  });

  it('marks one cheapest and one closest provider, and compares each to the typical price', () => {
    const { results, cheapest, typicalPrice } = search();
    expect(results.filter((r) => r.isCheapest).map((r) => r.id)).toEqual([cheapest.id]);
    expect(results.filter((r) => r.isClosest)).toHaveLength(1);
    expect(results[0].priceVsTypical).toBe(results[0].price - typicalPrice);
  });

  it('points to the closest option and a wider radius when nothing is in range', () => {
    const { results, closestOutside, widerRadiusMi } = findCare({
      serviceId: 'colonoscopy', origin: DULUTH, radiusMi: 5,
    });
    expect(results).toEqual([]);
    expect(closestOutside.distanceMi).toBeGreaterThan(5);
    expect(closestOutside.distanceMi).toBeLessThanOrEqual(widerRadiusMi);
  });
});
