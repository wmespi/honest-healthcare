import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';
import App from './App';
import { installDefaultMocks } from './test-support/mockApi';

vi.mock('./api');

beforeEach(() => {
  installDefaultMocks(api);
  api.getServiceLines.mockResolvedValue({ data: { imaging: { menu: [] } } });
});

describe('routing', () => {
  it('serves the imaging finder at its own address', async () => {
    window.history.replaceState(null, '', '/find-care/imaging');
    render(<App />);
    expect(await screen.findByLabelText('Which scan?')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/find-care/imaging');
  });

  it.each(['/', '/find-care', '/find-care/pcp', '/explore', '/nope'])('sends %s to the imaging finder', async (path) => {
    window.history.replaceState(null, '', path);
    render(<App />);
    expect(await screen.findByLabelText('Which scan?')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/find-care/imaging');
  });
});
