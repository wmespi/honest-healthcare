import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ImagingFinder } from './routes/ImagingFinder';

const FindCareMock = lazy(() => import('./mock/FindCareMock'));

const IMAGING = '/find-care/imaging';

// The imaging finder is the one working screen, so every other address lands on it.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path={IMAGING} element={<ImagingFinder />} />
        <Route path="/mock/find-care" element={<Suspense fallback={null}><FindCareMock /></Suspense>} />
        <Route path="*" element={<Navigate to={IMAGING} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
