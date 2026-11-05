import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ImagingFinder } from './routes/ImagingFinder';

const FindCare = lazy(() => import('./findCare/FindCare'));
const FindCareMock = lazy(() => import('./mock/FindCareMock'));

const IMAGING = '/find-care/imaging';

// The imaging finder is the working screen until the find-care screen replaces it (step 11
// of #135), so every other address still lands on it.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path={IMAGING} element={<ImagingFinder />} />
        <Route path="/find-care" element={<Suspense fallback={null}><FindCare /></Suspense>} />
        <Route path="/mock/find-care" element={<Suspense fallback={null}><FindCareMock /></Suspense>} />
        <Route path="*" element={<Navigate to={IMAGING} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
