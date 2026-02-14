# Frontend Service

React SPA for comparing healthcare costs across hospitals, payers, and procedures.

## Tech Stack

- **React 19** with Vite 7 (ES2020+, JSX)
- **Tailwind CSS 4** via @tailwindcss/vite plugin
- **Framer Motion** for animations
- **Axios** for API calls
- **Lucide React** for icons
- **clsx + tailwind-merge** for conditional class utilities

## Commands

```bash
npm install        # Install dependencies
npm run dev        # Start dev server (HMR)
npm run build      # Production build
npm run lint       # Run ESLint
npm run preview    # Preview production build
```

Or via Docker:
```bash
docker-compose up frontend
```

## Code Structure

- `src/App.jsx` — Main application component (filters, comparison UI, all logic)
- `src/api.js` — Axios client configured with `VITE_API_URL`
- `src/main.jsx` — React entry point
- `src/index.css` — Tailwind base + custom theme styles
- `index.html` — HTML template

## Configuration

- `vite.config.js` — Vite + React + Tailwind plugins
- `eslint.config.js` — ESLint flat config with React rules
- API URL set via `VITE_API_URL` env var (default: `http://localhost:8000`)

## Conventions

- All UI is currently in a single `App.jsx` — consider extracting components as it grows
- Styling uses Tailwind utility classes
- ESLint is configured; run `npm run lint` before committing
- No test framework configured yet (recommend Vitest)
- Production build is served via Nginx (see `deploy/Dockerfile.frontend`)
