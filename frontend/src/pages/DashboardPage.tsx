import { useEffect, useState } from "react";
import { format, subDays, startOfMonth, endOfMonth } from "date-fns";
import { toast } from "sonner";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line, Legend,
} from "recharts";
import { TrendingUp, TrendingDown, DollarSign, PiggyBank } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getTransactionSummary, listTransactions, type TransactionSummary } from "@/lib/api";
import { formatCurrency, formatPct } from "@/lib/utils";

interface CategorySpend { category: string; amount: number }
interface MonthlyTrend { month: string; expenses: number; income: number }

function KpiCard({
  title, value, sub, icon: Icon, loading,
}: {
  title: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-32" />
        ) : (
          <>
            <p className="text-2xl font-bold">{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];

export function DashboardPage() {
  const [summary, setSummary] = useState<TransactionSummary | null>(null);
  const [categoryData, setCategoryData] = useState<CategorySpend[]>([]);
  const [trendData, setTrendData] = useState<MonthlyTrend[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const today = new Date();
    const monthStart = format(startOfMonth(today), "yyyy-MM-dd");
    const monthEnd = format(endOfMonth(today), "yyyy-MM-dd");

    async function load() {
      try {
        const [sum, txns] = await Promise.all([
          getTransactionSummary(monthStart, monthEnd),
          listTransactions({ limit: 500, date_from: format(subDays(today, 180), "yyyy-MM-dd"), date_to: monthEnd }),
        ]);
        setSummary(sum);

        // Category aggregation (expenses only)
        const byCategory: Record<string, number> = {};
        for (const t of txns.items) {
          const amount = parseFloat(t.amount);
          if (amount < 0) {
            byCategory[t.category] = (byCategory[t.category] ?? 0) + Math.abs(amount);
          }
        }
        setCategoryData(
          Object.entries(byCategory)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([category, amount]) => ({ category, amount: parseFloat(amount.toFixed(2)) }))
        );

        // Monthly trend (last 6 months)
        const monthly: Record<string, { expenses: number; income: number }> = {};
        for (const t of txns.items) {
          const m = t.date.slice(0, 7); // yyyy-MM
          if (!monthly[m]) monthly[m] = { expenses: 0, income: 0 };
          const amount = parseFloat(t.amount);
          if (amount < 0) monthly[m].expenses += Math.abs(amount);
          else monthly[m].income += amount;
        }
        setTrendData(
          Object.entries(monthly)
            .sort(([a], [b]) => a.localeCompare(b))
            .slice(-6)
            .map(([month, v]) => ({
              month: format(new Date(month + "-01"), "MMM yy"),
              expenses: parseFloat(v.expenses.toFixed(2)),
              income: parseFloat(v.income.toFixed(2)),
            }))
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to load dashboard");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const income = summary ? parseFloat(summary.total_income) : 0;
  const expenses = summary ? Math.abs(parseFloat(summary.total_expenses)) : 0;
  const savings = summary ? parseFloat(summary.net_savings) : 0;
  const savingsRate = summary?.savings_rate;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground text-sm">Current month at a glance</p>
      </div>

      {/* KPI cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard title="Total Income" value={formatCurrency(income)} icon={TrendingUp} loading={loading} />
        <KpiCard title="Total Spent" value={formatCurrency(expenses)} icon={TrendingDown} loading={loading} />
        <KpiCard title="Net Savings" value={formatCurrency(savings)} icon={PiggyBank} loading={loading} />
        <KpiCard
          title="Savings Rate"
          value={savingsRate != null ? formatPct(savingsRate * 100, 1) : "—"}
          sub="of monthly income"
          icon={DollarSign}
          loading={loading}
        />
      </div>

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Spending by Category</CardTitle>
            <CardDescription>Last 6 months — expenses only</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={categoryData} layout="vertical" margin={{ left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tickFormatter={(v) => `$${v}`} fontSize={11} />
                  <YAxis dataKey="category" type="category" width={90} fontSize={11} />
                  <Tooltip formatter={(v) => formatCurrency(v as number)} />
                  {categoryData.map((_, i) => (
                    <Bar key={i} dataKey="amount" fill={COLORS[i % COLORS.length]} radius={[0, 4, 4, 0]} />
                  ))}
                  <Bar dataKey="amount" fill="#3b82f6" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Monthly Spending Trend</CardTitle>
            <CardDescription>Income vs expenses — last 6 months</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" fontSize={11} />
                  <YAxis tickFormatter={(v) => `$${v}`} fontSize={11} />
                  <Tooltip formatter={(v) => formatCurrency(v as number)} />
                  <Legend />
                  <Line type="monotone" dataKey="income" stroke="#10b981" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="expenses" stroke="#ef4444" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
