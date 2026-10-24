import { Link, NavLink } from "react-router-dom";
import { useFetch, useMeta, useTheme } from "../hooks";
import type { JobSummary } from "../types";
import { Mark } from "./Icons";

function RunningJob() {
  const { data: job } = useFetch<JobSummary | null>("/api/jobs/current", 2000);
  if (!job || job.status !== "running") return <span className="muted">idle</span>;
  const to = job.kind === "sweep" ? `/sweeps/${encodeURIComponent(job.params.label)}` : `/?job=${job.id}`;
  return (
    <Link to={to} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span className="spinner" />
      {job.kind} <span className="mono">{job.title}</span>
      {job.progress && (
        <span className="num">
          {job.progress.done}/{job.progress.total}
        </span>
      )}
    </Link>
  );
}

export function Header() {
  const meta = useMeta();
  const [theme, setTheme] = useTheme();
  return (
    <header className="topbar">
      <Link to="/" className="wordmark">
        <Mark />
        faultline
      </Link>
      <nav className="nav">
        <NavLink to="/" end>
          Playground
        </NavLink>
        <NavLink to="/sweeps">Sweeps</NavLink>
        <NavLink to="/compare">Compare</NavLink>
        <NavLink to="/catalog">Catalog</NavLink>
      </nav>
      <div className="topbar-right">
        <RunningJob />
        <span title={meta.llm_available ? "model used by the LLM architectures" : "set GEMINI_API_KEY in .env and restart"}>
          model <span className="mono">{meta.llm_available ? meta.model : "not configured"}</span>
        </span>
        <select
          aria-label="theme"
          value={theme}
          onChange={(e) => setTheme(e.target.value as typeof theme)}
          style={{ height: 26, fontSize: 12.5 }}
        >
          <option value="system">system theme</option>
          <option value="light">light</option>
          <option value="dark">dark</option>
        </select>
      </div>
    </header>
  );
}
