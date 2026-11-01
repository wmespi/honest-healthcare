import { describe, it, expect } from 'vitest';
import { defaultRadius, filterResults, findCare, resolveLocation, suggestLocations, suggestPlans, suggestServices } from './search';
import { LOCATIONS, PLANS, SERVICES, SERVICE_DETAILS } from './sampleData';

const VIRGINIA_HIGHLAND = resolveLocation('30306');
const EAST_ATLANTA = resolveLocation('30316');
const DULUTH = resolveLocation('30096');
const CANTON = resolveLocation('30114');

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

  it('find a scan by a word that is not in its name', () => {
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

  it('keeps only places offering the scan inside the radius', () => {
    const { results } = search();
    expect(results.length).toBeGreaterThan(0);
    expect(results.every((r) => r.distanceMi <= 10)).toBe(true);
    expect(results.every((r) => ['Imaging center', 'Hospital outpatient'].includes(r.kindLabel))).toBe(true);
    expect(search({ radiusMi: 25 }).results.length).toBeGreaterThan(results.length);
  });

  it('sorts by price by default and by distance on request', () => {
    const byPrice = search().results.map((r) => r.maxRate);
    expect(byPrice).toEqual([...byPrice].sort((a, b) => a - b));
    const byDistance = search({ sort: 'distance' }).results.map((r) => r.distanceMi);
    expect(byDistance).toEqual([...byDistance].sort((a, b) => a - b));
  });

  it('marks one cheapest and one closest place, and compares each to the median', () => {
    const { results, cheapest, nearbyMedian } = search();
    expect(results.filter((r) => r.isCheapest).map((r) => r.id)).toEqual([cheapest.id]);
    expect(results.filter((r) => r.isClosest)).toHaveLength(1);
    expect(results[0].maxVsMedian).toBe(results[0].maxRate - nearbyMedian);
  });

  it('prices vary by plan, but Medicare does not', () => {
    const silver = search({ planId: 'hartwell-silver' });
    const bronze = search({ planId: 'hartwell-bronze' });
    expect(silver.cheapest.maxRate).toBeLessThan(bronze.cheapest.maxRate);
    expect(silver.medicareRate).toBe(bronze.medicareRate);
    expect(search({ radiusMi: 25 }).medicareRate).toBe(search({ radiusMi: 5 }).medicareRate);
  });

  it('breaks the most you could pay into parts that add up to it exactly, for every scan and plan', () => {
    for (const service of SERVICES) {
      for (const plan of PLANS) {
        const { results } = findCare({ serviceId: service.id, planId: plan.id, origin: VIRGINIA_HIGHLAND, radiusMi: 50 });
        for (const location of results) {
          expect(location.parts.reduce((sum, part) => sum + part.amount, 0)).toBe(location.maxRate);
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

  it('orders each place\'s rates, and gives most places a single rate', () => {
    const { results } = search({ radiusMi: 50 });
    for (const r of results) {
      expect(r.minRate).toBeLessThanOrEqual(r.medianRate);
      expect(r.medianRate).toBeLessThanOrEqual(r.maxRate);
    }
    const singleRate = results.filter((r) => r.minRate === r.maxRate);
    expect(singleRate.length).toBeGreaterThan(results.length / 2);
  });

  it('lists each office of a practice as its own place, at the same price', () => {
    const offices = search({ radiusMi: 50 }).results.filter((r) => r.name === 'Dogwood Imaging');
    expect(offices.map((r) => r.neighborhood).sort()).toEqual(['Decatur', 'Tucker']);
    expect(offices[0].maxRate).toBe(offices[1].maxRate);
  });

  it('points to the closest option and a wider radius when nothing is in range', () => {
    const { results, closestOutside, widerRadiusMi } = findCare({
      serviceId: 'mri-lower-back', planId: 'blue-value', origin: CANTON, radiusMi: 5,
    });
    expect(results).toEqual([]);
    expect(closestOutside.distanceMi).toBeGreaterThan(5);
    expect(closestOutside.distanceMi).toBeLessThanOrEqual(widerRadiusMi);
  });
});

describe('filterResults', () => {
  const { results } = findCare({ serviceId: 'mri-lower-back', planId: 'blue-value', origin: VIRGINIA_HIGHLAND, radiusMi: 10 });

  it('keeps everything when empty', () => {
    expect(filterResults(results, '  ')).toBe(results);
  });

  it('finds a place by name', () => {
    expect(filterResults(results, 'beltline').map((r) => r.name)).toEqual(['Beltline Imaging']);
  });

  it('finds a place by a doctor there and lists that doctor first', () => {
    const place = results.find((r) => r.doctors.length > 1);
    const doctor = place.doctors[place.doctors.length - 1].name;
    const match = filterResults(results, doctor).find((r) => r.id === place.id);
    expect(match.matchedDoctor).toBe(doctor);
    expect(match.doctors[0].name).toBe(doctor);
    expect(match.doctors).toHaveLength(place.doctors.length);
  });

  it('returns nothing when no place or doctor matches', () => {
    expect(filterResults(results, 'no such name')).toEqual([]);
  });
});

describe('sample data', () => {
  it('has notes for every scan', () => {
    for (const service of SERVICES) expect(SERVICE_DETAILS[service.id]?.about).toBeTruthy();
  });

  it('gives every place doctors, and a rating only to hospitals', () => {
    for (const location of LOCATIONS) {
      expect(location.doctors.length).toBeGreaterThan(0);
      expect(location.stars !== null).toBe(location.kind === 'hospital');
    }
  });

  it('never repeats a doctor', () => {
    const names = LOCATIONS.flatMap((l) => l.doctors.map((d) => d.name));
    expect(new Set(names).size).toBe(names.length);
  });
});
