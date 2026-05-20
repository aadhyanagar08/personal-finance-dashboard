import { useState, useEffect } from "react";
import { Outlet, Navigate } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useAuth } from "@/contexts/AuthContext";

export function AppLayout() {
  const { isAuthenticated } = useAuth();
  const [dark, setDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar dark={dark} onToggleDark={() => setDark((d) => !d)} />
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
