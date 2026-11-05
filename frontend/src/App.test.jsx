import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';
import App from './App';

vi.mock('./api');
// MapLibre needs WebGL, which jsdom doesn't have.
vi.mock('./findCare/PriceMap', () => ({ PriceMap: () => null }));

beforeEach(() => {
  vi.resetAllMocks();
  api.getPlans.mockResolvedValue({ data: [] });
  api.getServices.mockResolvedValue({ data: [] });
});

describe('routing', () => {
  it.each(['/', '/find-care', '/find-care/imaging', '/mock/find-care', '/nope'])('sends %s to the find-care screen', async (path) => {
    window.history.replaceState(null, '', path);
    render(<App />);
    expect(await screen.findByText(/know the most you could pay/i)).toBeInTheDocument();
    expect(window.location.pathname).toBe('/find-care');
  });
});
