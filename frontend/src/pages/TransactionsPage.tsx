import { useEffect, useState, useCallback } from "react";
import { format, subDays } from "date-fns";
import type { DateRange } from "react-day-picker";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { listTransactions, type Transaction } from "@/lib/api";
import { formatCurrency, formatDate, cn } from "@/lib/utils";

const PAGE_SIZE = 25;

const CATEGORIES = [
  "All",
  "Housing", "Food", "Transport", "Entertainment",
  "Health", "Shopping", "Income", "Savings", "Uncategorized",
];

export function TransactionsPage() {
  const [items, setItems] = useState<Transaction[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState("All");
  const [dateRange, setDateRange] = useState<DateRange | undefined>({
    from: subDays(new Date(), 30),
    to: new Date(),
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const filters: Parameters<typeof listTransactions>[0] = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
      if (category !== "All") filters.category = category;
      if (dateRange?.from) filters.date_from = format(dateRange.from, "yyyy-MM-dd");
      if (dateRange?.to) filters.date_to = format(dateRange.to, "yyyy-MM-dd");

      const data = await listTransactions(filters);
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load transactions");
    } finally {
      setLoading(false);
    }
  }, [page, category, dateRange]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  function handleRangeChange(range: DateRange | undefined) {
    setDateRange(range);
    setPage(0);
  }

  function handleCategoryChange(val: string) {
    setCategory(val);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Transactions</h1>
        <p className="text-muted-foreground text-sm">Browse and filter your transaction history</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <DateRangePicker value={dateRange} onChange={handleRangeChange} />
        <Select value={category} onValueChange={handleCategoryChange}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Category" />
          </SelectTrigger>
          <SelectContent>
            {CATEGORIES.map((c) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground ml-auto">
          {total} transaction{total !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Transaction History</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <p className="py-12 text-center text-muted-foreground">No transactions found.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b bg-muted/40">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Date</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Description</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Category</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">Amount</th>
                    <th className="px-4 py-3 text-center font-medium text-muted-foreground">Flag</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {items.map((t) => (
                    <tr
                      key={t.id}
                      className={cn(
                        "transition-colors hover:bg-muted/30",
                        t.is_anomaly && "bg-red-50 dark:bg-red-950/20"
                      )}
                    >
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(t.date)}</td>
                      <td className="px-4 py-3 max-w-xs truncate">{t.description ?? "—"}</td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="font-normal">{t.category}</Badge>
                      </td>
                      <td className={cn(
                        "px-4 py-3 text-right font-mono font-medium whitespace-nowrap",
                        parseFloat(t.amount) < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"
                      )}>
                        {formatCurrency(t.amount)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {t.is_anomaly && (
                          <span title="Anomaly detected">
                            <AlertTriangle className="h-4 w-4 text-red-500 mx-auto" />
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setPage((p) => p - 1)} disabled={page === 0}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page + 1} of {totalPages}
          </span>
          <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page >= totalPages - 1}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
