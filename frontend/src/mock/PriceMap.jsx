import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { clsx } from 'clsx';
import { money } from './format';

// MapLibre looks for its worker beside its own file, which Vite moves when bundling.
maplibregl.setWorkerUrl(workerUrl);

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const METRO_ATLANTA = { center: [-84.39, 33.82], zoom: 9.3 };
const EMPTY = { type: 'FeatureCollection', features: [] };
const MILES_PER_DEGREE_LAT = 69;

function radiusRing({ lat, lon }, miles) {
  const dLat = miles / MILES_PER_DEGREE_LAT;
  const dLon = miles / (MILES_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180));
  return Array.from({ length: 73 }, (_, i) => {
    const angle = (i / 72) * 2 * Math.PI;
    return [lon + dLon * Math.cos(angle), lat + dLat * Math.sin(angle)];
  });
}

function pinClass({ cheapest, active }) {
  return clsx(
    'block cursor-pointer whitespace-nowrap rounded-full px-2.5 py-1 text-[13px] font-semibold leading-4 tabular-nums shadow-md ring-1 transition-colors duration-100',
    active && 'bg-slate-900 text-white ring-slate-900',
    !active && cheapest && 'bg-emerald-700 text-white ring-emerald-800',
    !active && !cheapest && 'bg-white text-slate-900 ring-slate-300',
  );
}

export function PriceMap({ origin, radiusMi, results, highlightId, selectedId, onHover, onSelect }) {
  const containerRef = useRef(null);
  const pinsRef = useRef(new Map());
  const handlersRef = useRef({ onHover, onSelect });
  const [map, setMap] = useState(null);

  useEffect(() => {
    handlersRef.current = { onHover, onSelect };
  });

  useEffect(() => {
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
  }, []);

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
    dot.style.zIndex = 900;
    const marker = new maplibregl.Marker({ element: dot }).setLngLat([origin.lon, origin.lat]).addTo(map);

    const bounds = ring.reduce(
      (box, point) => box.extend(point),
      new maplibregl.LngLatBounds(ring[0], ring[0]),
    );
    map.resize();
    map.fitBounds(bounds, { padding: 28, duration: 800 });
    return () => marker.remove();
  }, [map, origin, radiusMi]);

  // One price pin per provider. Cheaper pins sit on top where they overlap.
  useEffect(() => {
    if (!map) return undefined;
    const pins = pinsRef.current;
    const byPrice = [...results].sort((a, b) => b.price - a.price);
    byPrice.forEach((provider, index) => {
      const root = document.createElement('div');
      const pin = document.createElement('button');
      pin.type = 'button';
      pin.textContent = money(provider.price);
      pin.setAttribute('aria-label', `${provider.name}, ${money(provider.price)}`);
      pin.addEventListener('mouseenter', () => handlersRef.current.onHover(provider.id));
      pin.addEventListener('mouseleave', () => handlersRef.current.onHover(null));
      pin.addEventListener('click', (event) => {
        event.stopPropagation();
        handlersRef.current.onSelect(provider.id);
      });
      root.appendChild(pin);
      const marker = new maplibregl.Marker({ element: root }).setLngLat([provider.lon, provider.lat]).addTo(map);
      pins.set(provider.id, { marker, root, pin, cheapest: provider.isCheapest, baseZ: index + 1 });
    });
    return () => {
      pins.forEach(({ marker }) => marker.remove());
      pins.clear();
    };
  }, [map, results]);

  useEffect(() => {
    pinsRef.current.forEach(({ root, pin, cheapest, baseZ }, id) => {
      const active = id === highlightId || id === selectedId;
      pin.className = pinClass({ cheapest, active });
      root.style.zIndex = active ? 1000 : baseZ;
    });
  }, [map, results, highlightId, selectedId]);

  // Picking a row brings its pin into view, without moving a map that already shows it.
  useEffect(() => {
    if (!map || !selectedId) return;
    const provider = results.find((r) => r.id === selectedId);
    if (provider && !map.getBounds().contains([provider.lon, provider.lat])) {
      map.easeTo({ center: [provider.lon, provider.lat], duration: 500 });
    }
  }, [map, results, selectedId]);

  return <div ref={containerRef} className="size-full" />;
}
