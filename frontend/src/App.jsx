import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import FindCare from './findCare/FindCare';

const FIND_CARE = '/find-care';

// The find-care screen is the one screen, so every other address lands on it.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path={FIND_CARE} element={<FindCare />} />
        <Route path="*" element={<Navigate to={FIND_CARE} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
