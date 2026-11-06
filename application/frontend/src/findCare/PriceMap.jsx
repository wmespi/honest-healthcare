import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { clsx } from 'clsx';
import { money } from './format';
import { pinStack } from './view';

// MapLibre looks for its worker beside its own file, which Vite moves when bundling.
maplibregl.setWorkerUrl(workerUrl);

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const METRO_ATLANTA = { center: [-84.39, 33.82], zoom: 9.3 };
const EMPTY = { type: 'FeatureCollection', features: [] };
const MILES_PER_DEGREE_LAT = 69;
// How far apart pins at one point stack, a pin's height and a gap.
const PIN_STEP_PX = 28;

function radiusRing({ lat, lon }, miles) {
  const dLat = miles / MILES_PER_DEGREE_LAT;
  const dLon = miles / (MILES_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180));
  return Array.from({ length: 73 }, (_, i) => {
    const angle = (i / 72) * 2 * Math.PI;
    return [lon + dLon * Math.cos(angle), lat + dLat * Math.sin(angle)];
  });
}

function pinClass({ cheapest, incomplete, active }) {
  return clsx(
    'block cursor-pointer whitespace-nowrap rounded-full px-2.5 py-1 text-[13px] font-semibold leading-4 tabular-nums shadow-md ring-1 transition-colors duration-100',
    active && 'bg-slate-900 text-white ring-slate-900',
    !active && cheapest && 'bg-emerald-700 text-white ring-emerald-800',
    !active && incomplete && 'bg-slate-100 text-slate-500 ring-slate-300',
    !active && !cheapest && !incomplete && 'bg-white text-slate-900 ring-slate-300',
  );
}

// MapLibre draws with WebGL2 and throws without it, which would take the page down
// with the map, so the map checks first.
function canDrawMap() {
  try {
    return Boolean(document.createElement('canvas').getContext('webgl2'));
  } catch {
    return false;
  }
}

export function PriceMap({ origin, radiusMi, results, highlightId, selectedId, onHover, onSelect }) {
  const containerRef = useRef(null);
  const pinsRef = useRef(new Map());
  const handlersRef = useRef({ onHover, onSelect });
  const [map, setMap] = useState(null);
  const [drawable] = useState(canDrawMap);

  useEffect(() => {
    handlersRef.current = { onHover, onSelect };
  });

  useEffect(() => {
    if (!drawable) return undefined;
    const instance = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      ...METRO_ATLANTA,
      attributionControl: { compact: true },
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    instance.on('load', () => {
      // County lines read as marks on the search area, so they go.
      instance.setLayoutProperty('boundary_3', 'visibility', 'none');
      instance.addSource('radius', { type: 'geojson', data: EMPTY });
      instance.addLayer({
        id: 'radius-fill', type: 'fill', source: 'radius',
        paint: { 'fill-color': '#047857', 'fill-opacity': 0.04 },
      });
      instance.addLayer({
        id: 'radius-line', type: 'line', source: 'radius',
        paint: { 'line-color': '#047857', 'line-opacity': 0.55, 'line-width': 1.5, 'line-dasharray': [2, 2] },
      });
      setMap(instance);
    });
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      instance.remove();
    };
  }, [drawable]);

  // The search area: where the person is, how far they'll go, and a view that fits it.
  useEffect(() => {
    if (!map) return undefined;
    const source = map.getSource('radius');
    if (!origin) {
      source.setData(EMPTY);
      map.easeTo({ ...METRO_ATLANTA, duration: 600 });
      return undefined;
    }
    const ring = radiusRing(origin, radiusMi);
    source.setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] } });

    const dot = document.createElement('div');
    dot.className = 'size-4 rounded-full border-[3px] border-white bg-blue-600 shadow-md';
    dot.setAttribute('aria-label', 'Your location');
    // Under every pin, so it never covers a price; the ring's center still shows where it is.
    dot.style.zIndex = 0;
    const marker = new maplibregl.Marker({ element: dot }).setLngLat([origin.lon, origin.lat]).addTo(map);

    const bounds = ring.reduce(
      (box, point) => box.extend(point),
      new maplibregl.LngLatBounds(ring[0], ring[0]),
    );
    map.resize();
    map.fitBounds(bounds, { padding: 28, duration: 800 });
    return () => marker.remove();
  }, [map, origin, radiusMi]);

  // One price pin per place, stacked by pinStack where they overlap, with no amount on a
  // place with no full price. Places in one building stack their pins upward, so each stays
  // readable.
  useEffect(() => {
    if (!map) return undefined;
    const pins = pinsRef.current;
    const inBuilding = new Map();
    pinStack(results).forEach((location, index) => {
      const point = `${location.lat},${location.lon}`;
      const level = inBuilding.get(point) ?? 0;
      inBuilding.set(point, level + 1);
      const root = document.createElement('div');
      const pin = document.createElement('button');
      pin.type = 'button';
      const incomplete = location.maxRate === null;
      pin.textContent = incomplete ? '?' : money(location.maxRate);
      pin.setAttribute('aria-label', `${location.name}, ${incomplete ? 'price incomplete' : money(location.maxRate)}`);
      pin.addEventListener('mouseenter', () => handlersRef.current.onHover(location.id));
      pin.addEventListener('mouseleave', () => handlersRef.current.onHover(null));
      pin.addEventListener('click', (event) => {
        event.stopPropagation();
        handlersRef.current.onSelect(location.id);
      });
      root.appendChild(pin);
      const marker = new maplibregl.Marker({ element: root, offset: [0, -PIN_STEP_PX * level] })
        .setLngLat([location.lon, location.lat]).addTo(map);
      pins.set(location.id, { marker, root, pin, cheapest: location.isCheapest, incomplete, baseZ: index + 1 });
    });
    return () => {
      pins.forEach(({ marker }) => marker.remove());
      pins.clear();
    };
  }, [map, results]);

  useEffect(() => {
    pinsRef.current.forEach(({ root, pin, cheapest, incomplete, baseZ }, id) => {
      const active = id === highlightId || id === selectedId;
      pin.className = pinClass({ cheapest, incomplete, active });
      root.style.zIndex = active ? 1000 : baseZ;
    });
  }, [map, results, highlightId, selectedId]);

  // Picking a row brings its pin into view, without moving a map that already shows it.
  useEffect(() => {
    if (!map || !selectedId) return;
    const location = results.find((r) => r.id === selectedId);
    if (location && !map.getBounds().contains([location.lon, location.lat])) {
      map.easeTo({ center: [location.lon, location.lat], duration: 500 });
    }
  }, [map, results, selectedId]);

  if (!drawable) {
    return (
      <div className="flex size-full items-center justify-center bg-slate-50 px-6 text-center text-sm text-slate-500">
        This browser can&rsquo;t draw the map, but every place is in the list.
      </div>
    );
  }
  // `isolate` keeps pin z-indexes inside the map, so they can't show through the header's lists.
  return <div ref={containerRef} className="isolate size-full" />;
}
