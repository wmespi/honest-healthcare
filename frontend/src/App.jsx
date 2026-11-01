import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Explorer } from './routes/Explorer';
import { Landing } from './components/Landing';
import { ImagingFinder } from './routes/ImagingFinder';

const FindCareMock = lazy(() => import('./mock/FindCareMock'));

// Routes (#87): "/" leads with the task, "/find-care" is that task locked
// to the PCP service line (#83), "/find-care/imaging" prices a scan, "/explore" is the pre-existing general flow —
// demoted from front door to secondary path, otherwise unchanged. A stray path
// falls back to the landing rather than a dead end.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/find-care" element={<Explorer lockedServiceLine="pcp" />} />
        <Route path="/find-care/imaging" element={<ImagingFinder />} />
        <Route path="/find-care/pcp" element={<Navigate to="/find-care" replace />} />
        <Route path="/explore" element={<Explorer />} />
        <Route path="/mock/find-care" element={<Suspense fallback={null}><FindCareMock /></Suspense>} />
        <Route path="*" element={<Landing />} />
      </Routes>
    </BrowserRouter>
  );
}

export { Explorer };
