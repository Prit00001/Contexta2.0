import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Search } from "./pages/Search";
import { AskView } from "./pages/workspace/AskView";
import { DocumentsView } from "./pages/workspace/DocumentsView";
import { KeysView } from "./pages/workspace/KeysView";
import { MembersView } from "./pages/workspace/MembersView";
import { WorkspaceHome, WorkspaceLayout } from "./pages/workspace/WorkspaceLayout";

export function App() {
  const { pathname } = useLocation();
  return (
    <>
      <Nav />
      <ErrorBoundary resetKey={pathname}>
      <Routes>
        <Route path="/" element={<Search />} />
        <Route path="/workspace" element={<WorkspaceHome />} />
        <Route path="/workspace/:wsId" element={<WorkspaceLayout />}>
          <Route index element={<Navigate to="ask" replace />} />
          <Route path="ask/:convId?" element={<AskView />} />
          <Route path="documents" element={<DocumentsView />} />
          <Route path="members" element={<MembersView />} />
          <Route path="keys" element={<KeysView />} />
        </Route>
        <Route path="*" element={<p className="empty">Page not found. <Link to="/">Back to search</Link></p>} />
      </Routes>
      </ErrorBoundary>
    </>
  );
}

function Nav() {
  const { session, signOut } = useAuth();
  const cls = ({ isActive }: { isActive: boolean }) => `navlink${isActive ? " active" : ""}`;
  return (
    <nav className="nav" aria-label="Main">
      <Link to="/" className="brand">
        Contexta
      </Link>
      <NavLink to="/" end className={cls}>
        Papers
      </NavLink>
      <NavLink to="/workspace" className={cls}>
        Workspace
      </NavLink>
      {session && (
        <span className="who">
          <span className="email">{session.email || "Signed in"}</span>
          <button className="ghost" onClick={() => signOut()}>
            Sign out
          </button>
        </span>
      )}
    </nav>
  );
}
