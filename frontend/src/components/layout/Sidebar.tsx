import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  ArrowLeftRight,
  TrendingUp,
  Briefcase,
  LogOut,
  Moon,
  Sun,
  BarChart3,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/transactions", label: "Transactions", icon: ArrowLeftRight },
  { to: "/forecasts", label: "Forecasts", icon: TrendingUp },
  { to: "/portfolio", label: "Portfolio", icon: Briefcase },
];

interface SidebarProps {
  dark: boolean;
  onToggleDark: () => void;
}

export function Sidebar({ dark, onToggleDark }: SidebarProps) {
  const { user, logout } = useAuth();

  return (
    <aside className="flex h-screen w-60 flex-col border-r bg-card dark:bg-card">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2 px-5">
        <BarChart3 className="h-6 w-6 text-primary" />
        <span className="font-semibold text-sm tracking-tight">FinIntel</span>
      </div>

      <Separator />

      {/* Nav */}
      <nav className="flex-1 space-y-1 px-3 py-4">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>

      <Separator />

      {/* Footer */}
      <div className="space-y-1 px-3 py-4">
        <p className="px-3 text-xs text-muted-foreground truncate">{user?.email}</p>
        <Button variant="ghost" size="sm" className="w-full justify-start gap-3" onClick={onToggleDark}>
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          {dark ? "Light mode" : "Dark mode"}
        </Button>
        <Button variant="ghost" size="sm" className="w-full justify-start gap-3 text-destructive hover:text-destructive" onClick={logout}>
          <LogOut className="h-4 w-4" />
          Log out
        </Button>
      </div>
    </aside>
  );
}
