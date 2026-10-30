import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, isIntegrator, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { Loading } from "./components/ui";

// Chaque section est un module chargé à la demande (aucune page géante).
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Export = lazy(() => import("./pages/Export"));
const Sites = lazy(() => import("./pages/Sites"));
const Cameras = lazy(() => import("./pages/Cameras"));
const Users = lazy(() => import("./pages/Users"));
const Settings = lazy(() => import("./pages/Settings"));

function Routed() {
  const { me, checking } = useAuth();
  if (checking) return <Loading label="Vérification de la session…" />;
  if (!me) return <LoginPage />;
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="video" element={<Export />} />
          <Route path="sites" element={<Sites />} />
          <Route path="cameras" element={<Cameras />} />
          <Route path="enregistreurs" element={<Navigate to="/cameras" replace />} />
          <Route path="utilisateurs" element={isIntegrator(me) ? <Users /> : <Navigate to="/" replace />} />
          <Route path="parametres" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
