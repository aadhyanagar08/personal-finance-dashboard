import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  TrendingUp, TrendingDown, Activity, BarChart2, PiggyBank,
  DollarSign, Loader2, Search,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from "recharts";
import { formatCurrency, formatPct, cn } from "@/lib/utils";
import { getToken } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Holding {
  ticker: string;
  name: string | null;
  exchange: string | null;
  asset_type: string | null;
  quantity: number | null;
  current_price: number | null;
  market_price_currency: string | null;
  daily_change: number | null;
  daily_change_pct: number | null;
  book_value_cad: number | null;
  market_value_cad: number | null;
  unrealized_return_cad: number | null;
  unrealized_return_pct: number | null;
}

interface PortfolioOut {
  holdings: Holding[];
  total_assets: number;
  total_market_value_cad: number | null;
  total_book_value_cad: number | null;
  total_unrealized_cad: number | null;
  total_unrealized_pct: number | null;
}

interface PortfolioMetrics {
  total_value_cad: number | null;
  total_book_value_cad: number | null;
  total_unrealized_cad: number | null;
  total_unrealized_pct: number | null;
  daily_change: number | null;
  daily_change_pct: number | null;
  return_30d: number | null;
  return_90d: number | null;
  return_365d: number | null;
  volatility_30d: number | null;
  sharpe_ratio: number | null;
}

interface BenchmarkPoint { date: string; portfolio_value: number; benchmark_value: number }

interface BenchmarkComparison {
  days: number;
  benchmark_ticker: string;
  portfolio_return_pct: number | null;
  benchmark_return_pct: number | null;
  relative_return_pct: number | null;
  series: BenchmarkPoint[];
}

interface PriceClose { date: string; close: number }

interface CorrelationEntry {
  ticker: string;
  name: string | null;
  correlation: number | null;
}

interface PortfolioImpact {
  current_sharpe: number | null;
  pro_forma_sharpe: number | null;
  current_volatility_pct: number | null;
  pro_forma_volatility_pct: number | null;
  avg_correlation: number | null;
  sharpe_delta: number | null;
  volatility_delta_pct: number | null;
}

interface AnalysisResult {
  ticker: string;
  name: string | null;
  price_history: PriceClose[];
  correlations: CorrelationEntry[];
  portfolio_impact: PortfolioImpact;
  recommendation: string;
  recommendation_detail: string;
}

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string): Promise<T> {
  const token = getToken();
  const res = await fetch(`http://localhost:8000/api/v1${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail ?? res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function corrClass(corr: number): string {
  if (corr >= 0.6) return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300";
  if (corr >= 0.3) return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300";
  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300";
}

function recStyle(rec: string): string {
  if (rec.includes("low correlation"))
    return "border-l-4 border-emerald-500 bg-emerald-50 dark:bg-emerald-950/20";
  if (rec.includes("high correlation"))
    return "border-l-4 border-red-500 bg-red-50 dark:bg-red-950/20";
  return "border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-950/20";
}

function deltaColor(delta: number | null, higherIsBetter: boolean): string {
  if (delta == null) return "";
  const good = higherIsBetter ? delta > 0 : delta < 0;
  return good
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

// ---------------------------------------------------------------------------
// KPI card
// ---------------------------------------------------------------------------

function MetricCard({
  title, value, sub, positive, loading, icon: Icon,
}: {
  title: string; value: string; sub?: string;
  positive?: boolean | null; loading: boolean; icon: React.ElementType;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? <Skeleton className="h-8 w-28" /> : (
          <>
            <p className={cn(
              "text-2xl font-bold",
              positive === true && "text-emerald-600 dark:text-emerald-400",
              positive === false && "text-red-600 dark:text-red-400",
            )}>{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Stock Analyzer section
// ---------------------------------------------------------------------------

function StockAnalyzer() {
  const [input, setInput] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleAnalyze() {
    const t = input.trim();
    if (!t) return;
    setAnalyzing(true);
    setResult(null);
    apiFetch<AnalysisResult>(`/portfolio/analyze?ticker=${encodeURIComponent(t)}`)
      .then(setResult)
      .catch((err) => toast.error(err instanceof Error ? err.message : "Analysis failed"))
      .finally(() => setAnalyzing(false));
  }

  const impact = result?.portfolio_impact;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Search className="h-4 w-4" />
          Stock Analyzer
        </CardTitle>
        <CardDescription>
          Type any ticker to see its correlation with your holdings and how adding it
          would affect portfolio volatility and Sharpe ratio.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex gap-2">
          <Input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === "Enter") handleAnalyze(); }}
            placeholder="AAPL, SPY, QQQ, ZAG.TO …"
            className="max-w-xs font-mono"
          />
          <Button onClick={handleAnalyze} disabled={analyzing || !input.trim()}>
            {analyzing
              ? <><Loader2 className="h-4 w-4 mr-1.5 animate-spin" />Analyzing…</>
              : "Analyze"}
          </Button>
        </div>

        {analyzing && <Skeleton className="h-56 w-full" />}

        {result && !analyzing && (
          <div className="space-y-6">

            {/* Ticker header */}
            <div>
              <h3 className="font-bold text-lg font-mono">{result.ticker}</h3>
              {result.name && <p className="text-muted-foreground text-sm">{result.name}</p>}
            </div>

            {/* 1-year price chart */}
            {result.price_history.length > 0 && (
              <div>
                <p className="text-sm font-medium mb-2">1-Year Price History</p>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={result.price_history}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      dataKey="date"
                      fontSize={11}
                      tickFormatter={(d) => {
                        const dt = new Date(d + "T00:00:00");
                        return `${dt.getMonth() + 1}/${dt.getDate()}`;
                      }}
                      interval={Math.floor((result.price_history.length - 1) / 6)}
                    />
                    <YAxis
                      domain={["auto", "auto"]}
                      tickFormatter={(v) => v.toFixed(2)}
                      fontSize={11}
                      width={60}
                    />
                    <Tooltip
                      formatter={(v: number) => [v.toFixed(2), result.ticker]}
                      labelFormatter={(l) => `Date: ${l}`}
                    />
                    <Line
                      type="monotone"
                      dataKey="close"
                      stroke="#3b82f6"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Correlations + Impact side by side */}
            <div className="grid gap-6 sm:grid-cols-2">

              {/* Correlations */}
              <div>
                <p className="text-sm font-medium mb-3">Correlation with Holdings</p>
                <div className="space-y-1.5">
                  {result.correlations.map((c) => (
                    <div key={c.ticker} className="flex items-center gap-2 text-sm">
                      <span className="font-mono font-semibold w-12 shrink-0">{c.ticker}</span>
                      <span className="text-xs text-muted-foreground truncate flex-1">{c.name}</span>
                      <span className={cn(
                        "px-2 py-0.5 rounded text-xs font-mono font-medium shrink-0",
                        corrClass(c.correlation ?? 0)
                      )}>
                        {c.correlation != null ? c.correlation.toFixed(2) : "—"}
                      </span>
                    </div>
                  ))}
                  {result.correlations.length === 0 && (
                    <p className="text-xs text-muted-foreground">No overlapping price history found.</p>
                  )}
                </div>
                <div className="mt-3 flex gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-sm bg-emerald-400" />{"< 0.30 low"}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-sm bg-amber-400" />0.30–0.60
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-sm bg-red-400" />{"> 0.60 high"}
                  </span>
                </div>
              </div>

              {/* Portfolio Impact */}
              <div>
                <p className="text-sm font-medium mb-3">Portfolio Impact <span className="text-muted-foreground font-normal">(hypothetical 5% allocation)</span></p>
                {impact ? (
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Avg correlation</span>
                      <span className={cn("font-mono", corrClass(impact.avg_correlation ?? 0))}>
                        {impact.avg_correlation != null ? impact.avg_correlation.toFixed(2) : "—"}
                      </span>
                    </div>
                    <div className="border-t pt-2">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Sharpe (before)</span>
                        <span className="font-mono">{impact.current_sharpe?.toFixed(2) ?? "—"}</span>
                      </div>
                      <div className="flex justify-between mt-1">
                        <span className="text-muted-foreground">Sharpe (after)</span>
                        <span className={cn("font-mono", deltaColor(impact.sharpe_delta, true))}>
                          {impact.pro_forma_sharpe?.toFixed(2) ?? "—"}
                          {impact.sharpe_delta != null && (
                            <span className="ml-1 text-xs opacity-80">
                              ({impact.sharpe_delta >= 0 ? "+" : ""}{impact.sharpe_delta.toFixed(2)})
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                    <div className="border-t pt-2">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Volatility (before)</span>
                        <span className="font-mono">
                          {impact.current_volatility_pct != null ? `${impact.current_volatility_pct.toFixed(2)}%` : "—"}
                        </span>
                      </div>
                      <div className="flex justify-between mt-1">
                        <span className="text-muted-foreground">Volatility (after)</span>
                        <span className={cn("font-mono", deltaColor(impact.volatility_delta_pct, false))}>
                          {impact.pro_forma_volatility_pct != null ? `${impact.pro_forma_volatility_pct.toFixed(2)}%` : "—"}
                          {impact.volatility_delta_pct != null && (
                            <span className="ml-1 text-xs opacity-80">
                              ({impact.volatility_delta_pct >= 0 ? "+" : ""}{impact.volatility_delta_pct.toFixed(2)}%)
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">Insufficient overlapping data for impact analysis.</p>
                )}
              </div>
            </div>

            {/* Recommendation */}
            <div className={cn("rounded-lg p-4", recStyle(result.recommendation))}>
              <p className="font-semibold text-sm capitalize">{result.recommendation}</p>
              <p className="text-muted-foreground text-xs mt-1 leading-relaxed">
                {result.recommendation_detail}
              </p>
            </div>

          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const BENCHMARK_DAYS_OPTIONS = [
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
  { label: "365 days", value: 365 },
];

export function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<PortfolioOut | null>(null);
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [benchmark, setBenchmark] = useState<BenchmarkComparison | null>(null);
  const [benchDays, setBenchDays] = useState(90);
  const [benchTicker, setBenchTicker] = useState("XIC");
  const [benchTickerInput, setBenchTickerInput] = useState("XIC");
  const [loading, setLoading] = useState(true);
  const [benchLoading, setBenchLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      apiFetch<PortfolioOut>("/portfolio"),
      apiFetch<PortfolioMetrics>("/portfolio/metrics"),
    ])
      .then(([p, m]) => { setPortfolio(p); setMetrics(m); })
      .catch((err) => toast.error(err instanceof Error ? err.message : "Failed to load portfolio"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setBenchLoading(true);
    apiFetch<BenchmarkComparison>(
      `/portfolio/benchmark?days=${benchDays}&ticker=${encodeURIComponent(benchTicker)}`
    )
      .then(setBenchmark)
      .catch((err) => toast.error(err instanceof Error ? err.message : "Failed to load benchmark"))
      .finally(() => setBenchLoading(false));
  }, [benchDays, benchTicker]);

  function commitBenchTicker() {
    const t = benchTickerInput.trim().toUpperCase();
    if (t && t !== benchTicker) setBenchTicker(t);
  }

  const ur = metrics?.total_unrealized_cad ?? 0;
  const urPos = ur >= 0;
  const dailyPos = (metrics?.daily_change ?? 0) >= 0;
  const r30Pos = (metrics?.return_30d ?? 0) >= 0;

  const bmLabel = benchmark?.benchmark_ticker ?? benchTicker;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-muted-foreground text-sm">Live prices via yfinance · Sharpe uses 4.5% risk-free rate</p>
      </div>

      {/* KPI row */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="Total Value (CAD)"
          value={metrics?.total_value_cad != null ? formatCurrency(metrics.total_value_cad) : "—"}
          sub={metrics?.total_book_value_cad != null ? `Cost: ${formatCurrency(metrics.total_book_value_cad)}` : undefined}
          loading={loading}
          icon={BarChart2}
        />
        <MetricCard
          title="Unrealized P&L"
          value={metrics?.total_unrealized_cad != null ? formatCurrency(metrics.total_unrealized_cad) : "—"}
          sub={metrics?.total_unrealized_pct != null ? formatPct(metrics.total_unrealized_pct) : undefined}
          positive={urPos}
          loading={loading}
          icon={PiggyBank}
        />
        <MetricCard
          title="Daily Change"
          value={metrics?.daily_change != null ? formatCurrency(metrics.daily_change) : "—"}
          sub={metrics?.daily_change_pct != null ? formatPct(metrics.daily_change_pct) : undefined}
          positive={dailyPos}
          loading={loading}
          icon={dailyPos ? TrendingUp : TrendingDown}
        />
        <MetricCard
          title="30-day Return"
          value={metrics?.return_30d != null ? formatPct(metrics.return_30d) : "—"}
          sub={metrics?.sharpe_ratio != null ? `Sharpe: ${metrics.sharpe_ratio.toFixed(2)}` : undefined}
          positive={r30Pos}
          loading={loading}
          icon={Activity}
        />
      </div>

      {/* Metrics row 2 */}
      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard
          title="90-day Return"
          value={metrics?.return_90d != null ? formatPct(metrics.return_90d) : "—"}
          positive={(metrics?.return_90d ?? 0) >= 0}
          loading={loading}
          icon={TrendingUp}
        />
        <MetricCard
          title="1-year Return"
          value={metrics?.return_365d != null ? formatPct(metrics.return_365d) : "—"}
          positive={(metrics?.return_365d ?? 0) >= 0}
          loading={loading}
          icon={TrendingUp}
        />
        <MetricCard
          title="Annualised Volatility"
          value={metrics?.volatility_30d != null ? formatPct(metrics.volatility_30d) : "—"}
          loading={loading}
          icon={DollarSign}
        />
      </div>

      {/* Benchmark chart */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <CardTitle className="text-base">Portfolio vs {bmLabel} Benchmark</CardTitle>
              <CardDescription>
                {benchmark && (
                  <>
                    Portfolio {formatPct(benchmark.portfolio_return_pct ?? 0)} ·{" "}
                    {bmLabel} {formatPct(benchmark.benchmark_return_pct ?? 0)} ·{" "}
                    Alpha{" "}
                    <span className={cn(
                      (benchmark.relative_return_pct ?? 0) >= 0
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-red-600 dark:text-red-400"
                    )}>
                      {formatPct(benchmark.relative_return_pct ?? 0)}
                    </span>
                  </>
                )}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Input
                value={benchTickerInput}
                onChange={(e) => setBenchTickerInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => { if (e.key === "Enter") commitBenchTicker(); }}
                onBlur={commitBenchTicker}
                placeholder="XIC"
                className="w-24 h-9 text-sm font-mono"
                title="Benchmark ticker — press Enter to apply"
              />
              <Select value={String(benchDays)} onValueChange={(v) => setBenchDays(Number(v))}>
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BENCHMARK_DAYS_OPTIONS.map(({ label, value }) => (
                    <SelectItem key={value} value={String(value)}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {benchLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : !benchmark?.series.length ? (
            <p className="py-12 text-center text-muted-foreground">No benchmark data available.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={benchmark.series}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  fontSize={11}
                  tickFormatter={(d) => {
                    const dt = new Date(d + "T00:00:00");
                    return `${dt.getMonth() + 1}/${dt.getDate()}`;
                  }}
                  interval={Math.floor((benchmark.series.length - 1) / 6)}
                />
                <YAxis domain={["auto", "auto"]} tickFormatter={(v) => `${v.toFixed(0)}`} fontSize={11} />
                <Tooltip
                  formatter={(v: number, name: string) => [
                    `${v.toFixed(2)}`,
                    name === "portfolio_value" ? "Portfolio" : bmLabel,
                  ]}
                  labelFormatter={(l) => `Date: ${l}`}
                />
                <Legend formatter={(v) => v === "portfolio_value" ? "Portfolio" : bmLabel} />
                <ReferenceLine y={100} stroke="hsl(var(--border))" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="portfolio_value" stroke="#3b82f6" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="benchmark_value" stroke="#f59e0b" strokeWidth={2} dot={false} strokeDasharray="5 3" />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Holdings table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Holdings</CardTitle>
          {portfolio && (
            <CardDescription>
              {portfolio.total_assets} positions ·{" "}
              Market value {formatCurrency(portfolio.total_market_value_cad ?? 0)} CAD ·{" "}
              <span className={cn(ur >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                {formatCurrency(portfolio.total_unrealized_cad ?? 0)} ({formatPct(portfolio.total_unrealized_pct ?? 0)})
              </span>
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : !portfolio?.holdings.length ? (
            <p className="py-12 text-center text-muted-foreground">No holdings found.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b bg-muted/40">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Symbol</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Name</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Exchange</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Qty</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Price</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Day %</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Mkt Val (CAD)</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Book (CAD)</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">P&amp;L (CAD)</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">P&amp;L %</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {portfolio.holdings.map((h) => {
                    const dayUp = (h.daily_change_pct ?? 0) >= 0;
                    const plUp = (h.unrealized_return_cad ?? 0) >= 0;
                    return (
                      <tr key={h.ticker} className="hover:bg-muted/30 transition-colors">
                        <td className="px-4 py-3 font-mono font-bold">{h.ticker}</td>
                        <td className="px-4 py-3 max-w-[180px] truncate text-muted-foreground text-xs">{h.name ?? "—"}</td>
                        <td className="px-4 py-3">
                          <Badge variant="outline" className="text-xs font-normal">{h.exchange ?? "—"}</Badge>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-muted-foreground">
                          {h.quantity?.toFixed(4) ?? "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">
                          {h.current_price != null
                            ? `${h.market_price_currency ?? "CAD"} ${h.current_price.toFixed(2)}`
                            : "—"}
                        </td>
                        <td className={cn(
                          "px-4 py-3 text-right font-mono",
                          h.daily_change_pct != null && (dayUp ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")
                        )}>
                          {h.daily_change_pct != null ? formatPct(h.daily_change_pct) : "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">
                          {h.market_value_cad != null ? formatCurrency(h.market_value_cad) : "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-muted-foreground">
                          {h.book_value_cad != null ? formatCurrency(h.book_value_cad) : "—"}
                        </td>
                        <td className={cn(
                          "px-4 py-3 text-right font-mono font-medium",
                          h.unrealized_return_cad != null && (plUp ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")
                        )}>
                          {h.unrealized_return_cad != null ? formatCurrency(h.unrealized_return_cad) : "—"}
                        </td>
                        <td className={cn(
                          "px-4 py-3 text-right font-mono",
                          h.unrealized_return_pct != null && (plUp ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")
                        )}>
                          {h.unrealized_return_pct != null ? formatPct(h.unrealized_return_pct) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Stock Analyzer */}
      <StockAnalyzer />
    </div>
  );
}
