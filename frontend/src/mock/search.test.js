import { describe, it, expect } from 'vitest';
import { defaultRadius, findCare, resolveLocation, suggestLocations, suggestPlans, suggestServices } from './search';
import { PLANS, SERVICES, SERVICE_DETAILS } from './sampleData';

const VIRGINIA_HIGHLAND = resolveLocation('30306');
const EAST_ATLANTA = resolveLocation('30316');
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

  it('find a plan by its carrier', () => {
    expect(suggestPlans('anthem').map((o) => o.id)).toEqual(['blue-value', 'pathway']);
  });
});

describe('defaultRadius', () => {
  it('stays local where there are enough choices and widens where there are not', () => {
    expect(defaultRadius({ serviceId: 'mri-lower-back', origin: EAST_ATLANTA })).toBe(5);
    expect(defaultRadius({ serviceId: 'mri-lower-back', origin: DULUTH })).toBe(25);
  });
});

describe('findCare', () => {
  const search = (overrides) => findCare({
    serviceId: 'mri-lower-back', planId: 'blue-value', origin: VIRGINIA_HIGHLAND, radiusMi: 10, ...overrides,
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

  it('marks one cheapest and one closest provider, and compares each to the median', () => {
    const { results, cheapest, medianPrice } = search();
    expect(results.filter((r) => r.isCheapest).map((r) => r.id)).toEqual([cheapest.id]);
    expect(results.filter((r) => r.isClosest)).toHaveLength(1);
    expect(results[0].priceVsMedian).toBe(results[0].price - medianPrice);
  });

  it('prices vary by plan, but Medicare does not', () => {
    const silver = search({ planId: 'hartwell-silver' });
    const bronze = search({ planId: 'hartwell-bronze' });
    expect(silver.cheapest.price).toBeLessThan(bronze.cheapest.price);
    expect(silver.medicareRate).toBe(bronze.medicareRate);
    expect(search({ radiusMi: 25 }).medicareRate).toBe(search({ radiusMi: 5 }).medicareRate);
  });

  it('breaks every price into parts that add up to it exactly, for every service and plan', () => {
    for (const service of SERVICES) {
      for (const plan of PLANS) {
        const { results } = findCare({ serviceId: service.id, planId: plan.id, origin: VIRGINIA_HIGHLAND, radiusMi: 50 });
        for (const provider of results) {
          expect(provider.parts.reduce((sum, part) => sum + part.amount, 0)).toBe(provider.price);
        }
      }
    }
  });

  it('adds a facility fee to a hospital scan, but not to an imaging center scan', () => {
    const { results } = search();
    const labels = (kind) => results.find((r) => r.kindLabel === kind).parts.map((p) => p.label);
    expect(labels('Hospital outpatient')).toContain('Hospital facility fee');
    expect(labels('Imaging center')).not.toContain('Hospital facility fee');
  });

  it('points to the closest option and a wider radius when nothing is in range', () => {
    const { results, closestOutside, widerRadiusMi } = findCare({
      serviceId: 'colonoscopy', planId: 'blue-value', origin: DULUTH, radiusMi: 5,
    });
    expect(results).toEqual([]);
    expect(closestOutside.distanceMi).toBeGreaterThan(5);
    expect(closestOutside.distanceMi).toBeLessThanOrEqual(widerRadiusMi);
  });
});

describe('sample data', () => {
  it('has notes for every service', () => {
    for (const service of SERVICES) expect(SERVICE_DETAILS[service.id]?.about).toBeTruthy();
  });
});
