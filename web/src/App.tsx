import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { MetaContext, useFetch } from "./hooks";
import { Catalog } from "./pages/Catalog";
import { Playground } from "./pages/Playground";
import { SweepDetail } from "./pages/SweepDetail";
import { Sweeps } from "./pages/Sweeps";
import { TrialPage } from "./pages/TrialPage";
import type { Meta } from "./types";

export default function App() {
  const { data: meta, error } = useFetch<Meta>("/api/meta");

  if (error) {
    return (
      <div className="page">
        <div className="notice error">
          Can't reach the faultline API ({error}). Start it with <code>python -m api</code>.
        </div>
      </div>
    );
  }
  if (!meta) return null;

  return (
    <MetaContext.Provider value={meta}>
      <BrowserRouter>
        <Header />
        <Routes>
          <Route path="/" element={<Playground />} />
          <Route path="/sweeps" element={<Sweeps />} />
          <Route path="/sweeps/:label" element={<SweepDetail />} />
          <Route path="/trials/:id" element={<TrialPage />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route
            path="*"
            element={
              <div className="page">
                <div className="empty">Nothing here.</div>
              </div>
            }
          />
        </Routes>
      </BrowserRouter>
    </MetaContext.Provider>
  );
}
