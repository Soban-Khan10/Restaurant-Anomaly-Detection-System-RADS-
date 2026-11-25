import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { LiveProvider } from "./live/LiveContext";
import { rolesFor } from "./nav";
import IncidentDetailPage from "./pages/IncidentDetailPage";
import IncidentsPage from "./pages/IncidentsPage";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import CameraAreasPage from "./pages/CameraAreasPage";
import CamerasPage from "./pages/CamerasPage";
import { NoAccess, NotFound } from "./pages/Placeholders";
import RulesPage from "./pages/RulesPage";
import ReportsPage from "./pages/ReportsPage";
import UsersPage from "./pages/UsersPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "checking") {
    return <div className="flex min-h-screen items-center justify-center"><Spinner label="Opening RADS…" /></div>;
  }
  if (status === "signed-out") return <Navigate to="/login" replace state={{ from: location }} />;
  return <>{children}</>;
}

/** Shows the page only to the roles that have it in their menu (the server checks again on every request). */
function RoleGate({ path, children }: { path: string; children: ReactNode }) {
  const { hasRole } = useAuth();
  return hasRole(...rolesFor(path)) ? <>{children}</> : <NoAccess />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><LiveProvider><Layout /></LiveProvider></RequireAuth>}>
        <Route index element={<OverviewPage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:id" element={<IncidentDetailPage />} />
        <Route path="cameras" element={<RoleGate path="/cameras"><CamerasPage /></RoleGate>} />
        <Route path="cameras/:id" element={<RoleGate path="/cameras"><CameraAreasPage /></RoleGate>} />
        <Route path="rules" element={<RulesPage />} />
        <Route path="reports" element={<RoleGate path="/reports"><ReportsPage /></RoleGate>} />
        <Route path="users" element={<RoleGate path="/users"><UsersPage /></RoleGate>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
