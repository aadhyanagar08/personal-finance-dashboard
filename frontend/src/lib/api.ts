const BASE = "http://localhost:8000/api/v1";

let _token: string | null = null;

export function setToken(t: string | null) {
  _token = t;
}
export function getToken() {
  return _token;
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (_token) headers["Authorization"] = `Bearer ${_token}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail ?? res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserOut {
  id: number;
  email: string;
  is_active: boolean;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail ?? "Login failed");
  }
  return res.json();
}

export async function getMe(): Promise<UserOut> {
  return req("/auth/me");
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

export interface Transaction {
  id: number;
  date: string;
  amount: string;
  category: string;
  description: string | null;
  source: string | null;
  is_anomaly: boolean;
}

export interface PaginatedTransactions {
  total: number;
  limit: number;
  offset: number;
  items: Transaction[];
}

export interface TransactionSummary {
  date_from: string;
  date_to: string;
  total_income: string;
  total_expenses: string;
  net_savings: string;
  savings_rate: number | null;
}

export interface TransactionFilters {
  limit?: number;
  offset?: number;
  date_from?: string;
  date_to?: string;
  category?: string;
  is_anomaly?: boolean;
}

export function listTransactions(filters: TransactionFilters = {}): Promise<PaginatedTransactions> {
  const p = new URLSearchParams();
  if (filters.limit != null) p.set("limit", String(filters.limit));
  if (filters.offset != null) p.set("offset", String(filters.offset));
  if (filters.date_from) p.set("date_from", filters.date_from);
  if (filters.date_to) p.set("date_to", filters.date_to);
  if (filters.category) p.set("category", filters.category);
  if (filters.is_anomaly != null) p.set("is_anomaly", String(filters.is_anomaly));
  const qs = p.toString();
  return req(`/transactions${qs ? "?" + qs : ""}`);
}

export function getTransactionSummary(date_from: string, date_to: string): Promise<TransactionSummary> {
  return req(`/transactions/summary?date_from=${date_from}&date_to=${date_to}`);
}

// ---------------------------------------------------------------------------
// Forecasts
// ---------------------------------------------------------------------------

export interface ForecastPoint {
  forecast_date: string;
  predicted_amount: string;
  confidence_lower: string | null;
  confidence_upper: string | null;
}

export interface CategoryForecast {
  category: string;
  periods: number;
  points: ForecastPoint[];
}

export interface CategorySummary {
  category: string;
  projected_30d_spend: string;
}

export interface ForecastSummary {
  date_from: string;
  date_to: string;
  categories: CategorySummary[];
}

export function getForecastSummary(): Promise<ForecastSummary> {
  return req("/forecasts/summary");
}

export function getCategoryForecast(category: string, periods = 90): Promise<CategoryForecast> {
  return req(`/forecasts/${encodeURIComponent(category)}?periods=${periods}`);
}

export function refreshForecasts(): Promise<{ status: string; categories_queued: number }> {
  return req("/forecasts/refresh", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Portfolio
// ---------------------------------------------------------------------------

export interface Holding {
  ticker: string;
  name: string | null;
  asset_type: string | null;
  current_price: number | null;
  daily_change: number | null;
  daily_change_pct: number | null;
}

export interface PortfolioOut {
  holdings: Holding[];
  total_assets: number;
}

export interface PortfolioMetrics {
  total_value: number | null;
  daily_change: number | null;
  daily_change_pct: number | null;
  return_30d: number | null;
  volatility_30d: number | null;
  sharpe_ratio: number | null;
}

export function getPortfolio(): Promise<PortfolioOut> {
  return req("/portfolio");
}

export function getPortfolioMetrics(): Promise<PortfolioMetrics> {
  return req("/portfolio/metrics");
}
