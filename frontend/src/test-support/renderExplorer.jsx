import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Explorer } from '../routes/Explorer';

// A MemoryRouter is enough context for Explorer's useNavigate() calls without
// any route-matching ceremony — routing itself (the landing, /find-care,
// /explore) is exercised against the real `<App/>` in App.test.jsx instead.
export function renderExplorer(props) {
  return render(<MemoryRouter><Explorer {...props} /></MemoryRouter>);
}
