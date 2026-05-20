import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import {
  Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Area, ComposedChart,
} from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getForecastSummary, getCategoryForecast, refreshForecasts, type CategoryForecast } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";

const KNOWN_CATEGORIES = ["Food", "Housing", "Transport", "Entertainment", "Health", "Shopping", "Savings"];

interface ChartPoint {
  date: string;
  predicted: number;
  lower: number | null;
  upper: number | null;
}

export function ForecastsPage() {
  const [categories, setCategories] = useState<string[]>(KNOWN_CATEGORIES);
  const [selected, setSelected] = useState(KNOWN_CATEGORIES[0]);
  const [forecast, setForecast] = useState<CategoryForecast | null>(null);
  const [chartData, setChartData] = useState<ChartPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [notFound, setNotFound] = useState(false);

  // Load available categories from summary
  useEffect(() => {
    getForecastSummary()
      .then((s) => {
        if (s.categories.length > 0) {
          const cats = s.categories.map((c) => c.category);
          setCategories(cats);
          setSelected(cats[0]);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    getCategoryForecast(selected)
      .then((fc) => {
        setForecast(fc);
        setChartData(
          fc.points.map((p) => ({
            date: p.forecast_date,
            predicted: parseFloat(p.predicted_amount),
            lower: p.confidence_lower != null ? parseFloat(p.confidence_lower) : null,
            upper: p.confidence_upper != null ? parseFloat(p.confidence_upper) : null,
          }))
        );
      })
      .catch((err) => {
        if (err.message?.includes("404") || err.message?.toLowerCase().includes("not found") || err.message?.toLowerCase().includes("no forecast")) {
          setNotFound(true);
        } else {
          toast.error(err instanceof Error ? err.message : "Failed to load forecast");
        }
        setForecast(null);
        setChartData([]);
      })
      .finally(() => setLoading(false));
  }, [selected]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const res = await refreshForecasts();
      toast.success(`Refresh queued for ${res.categories_queued} categories`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Forecasts</h1>
          <p className="text-muted-foreground text-sm">90-day Prophet forecast with 95% confidence bands</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
          <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Retrain models
        </Button>
      </div>

      {/* Category selector */}
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium">Category</span>
        <Select value={selected} onValueChange={setSelected}>
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {forecast && (
          <span className="text-xs text-muted-foreground">{forecast.periods} data points</span>
        )}
      </div>

      {/* Forecast chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{selected} — 90-day Forecast</CardTitle>
          <CardDescription>Shaded area represents the 95% confidence interval</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-80 w-full" />
          ) : notFound ? (
            <div className="flex flex-col items-center justify-center h-80 gap-3 text-muted-foreground">
              <p>No forecast data for <strong>{selected}</strong>.</p>
              <p className="text-sm">Click "Retrain models" to generate forecasts.</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  fontSize={11}
                  tickFormatter={(d) => {
                    const dt = new Date(d + "T00:00:00");
                    return `${dt.getMonth() + 1}/${dt.getDate()}`;
                  }}
                  interval={Math.floor(chartData.length / 8)}
                />
                <YAxis tickFormatter={(v) => `$${v}`} fontSize={11} />
                <Tooltip
                  formatter={(value, name) => {
                    const labels: Record<string, string> = { predicted: "Forecast", lower: "Lower 95%", upper: "Upper 95%" };
                    return [formatCurrency(value as number), labels[name as string] ?? name];
                  }}
                  labelFormatter={(label) => `Date: ${label}`}
                />
                {/* Confidence band */}
                <Area
                  type="monotone"
                  dataKey="upper"
                  stroke="none"
                  fill="#3b82f6"
                  fillOpacity={0.15}
                  legendType="none"
                  connectNulls
                />
                <Area
                  type="monotone"
                  dataKey="lower"
                  stroke="none"
                  fill="#ffffff"
                  fillOpacity={1}
                  legendType="none"
                  connectNulls
                />
                <Line
                  type="monotone"
                  dataKey="predicted"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={false}
                  name="Forecast"
                />
                <ReferenceLine y={0} stroke="hsl(var(--border))" />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
