import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Explorer } from './routes/Explorer';
import { Landing } from './components/Landing';

// Routes (#87): "/" leads with the task, "/find-care/pcp" is that task locked
// to the PCP service line (#83), "/explore" is the pre-existing general flow —
// demoted from front door to secondary path, otherwise unchanged. A stray path
// falls back to the landing rather than a dead end.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/find-care/pcp" element={<Explorer lockedServiceLine="pcp" />} />
        <Route path="/explore" element={<Explorer />} />
        <Route path="*" element={<Landing />} />
      </Routes>
    </BrowserRouter>
  );
}

export { Explorer };
