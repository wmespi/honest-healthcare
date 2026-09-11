import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as api from './api';
import App from './App';
import { BV, installDefaultMocks } from './test-support/explorerFixtures';

vi.mock('./api');

// App is routing + providers only (#100/#101) — the rate explorer itself is
// routes/Explorer.jsx, exercised directly (no route-matching ceremony needed)
// in routes/Explorer.test.jsx. This file is #87's routing behavior: does the
// landing show, and do its two links land on the right locked/unlocked
// Explorer. Both describe blocks share the same API mocks (navigating to
// /find-care/pcp renders the real Explorer, which needs them).
beforeEach(() => installDefaultMocks(api));

describe('routing (#87) — task-first landing', () => {
  beforeEach(() => { window.history.replaceState(null, '', '/'); });

  it('shows the task-first landing at /, not the explorer', () => {
    render(<App />);
    expect(screen.getByText(/what are you trying to do/i)).toBeInTheDocument();
    expect(screen.getByText(/find a primary care doctor/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search procedure or billing code/i)).not.toBeInTheDocument();
  });

  it('the PCP card navigates to /find-care/pcp and shows the locked, PCP-scoped explorer', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText(/find a primary care doctor/i));

    expect(window.location.pathname).toBe('/find-care/pcp');
    await waitFor(() => expect(api.searchProviders).toHaveBeenCalledWith('', '', 40, BV, 'pcp'));
    expect(await screen.findByText('ABBOTT, ASHLEY')).toBeInTheDocument();
    expect(screen.getAllByText('Primary Care (PCP)').length).toBeGreaterThan(0);
  });

  it('the secondary link navigates to /explore, the general flow', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText(/explore all networks and procedures/i));

    expect(window.location.pathname).toBe('/explore');
    expect(await screen.findByPlaceholderText(/search procedure or billing code/i)).toBeInTheDocument();
  });

  it('an unknown path falls back to the landing rather than a dead end', () => {
    window.history.replaceState(null, '', '/nope');
    render(<App />);
    expect(screen.getByText(/what are you trying to do/i)).toBeInTheDocument();
  });
});
