/**
 * react-query hooks for every list and detail the screens read.
 *
 * Keys are arrays starting with the resource name so a mutation can
 * invalidate a whole resource (`["imports"]`) or one item (`["imports", id]`).
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import type { Page, Query } from "./api";
import type {
  AuditPage,
  AvailabilityEvent,
  Dashboard,
  ImportJob,
  ImportProfile,
  ImportReport,
  ImportRow,
  MappingException,
  MappingExceptionDetail,
  RuleSchemas,
  StatusHistory,
  Vendor,
  WatchlistEntry,
} from "./types";

export const keys = {
  vendors: (query?: Query) => ["vendors", query ?? {}] as const,
  vendor: (id: string) => ["vendors", id] as const,
  profiles: (vendorId: string, includeInactive: boolean) =>
    ["profiles", vendorId, includeInactive] as const,
  ruleSchemas: ["rule-schemas"] as const,
  imports: (query?: Query) => ["imports", query ?? {}] as const,
  importJob: (id: string) => ["imports", id] as const,
  importReport: (id: string) => ["imports", id, "report"] as const,
  importRows: (id: string, query?: Query) => ["imports", id, "rows", query ?? {}] as const,
  exceptions: (query?: Query) => ["exceptions", query ?? {}] as const,
  exception: (id: string) => ["exceptions", id] as const,
  watchlist: (query?: Query) => ["watchlist", query ?? {}] as const,
  watchHistory: (id: string) => ["watchlist", id, "history"] as const,
  events: (query?: Query) => ["availability", query ?? {}] as const,
  audit: (query?: Query) => ["audit", query ?? {}] as const,
  dashboard: ["dashboard"] as const,
};

export function useVendors(query?: Query) {
  return useQuery({ queryKey: keys.vendors(query), queryFn: () => api.get<Page<Vendor>>("/vendors", query) });
}

export function useVendor(id: string | null) {
  return useQuery({
    queryKey: keys.vendor(id ?? ""),
    queryFn: () => api.get<Vendor>(`/vendors/${id}`),
    enabled: id !== null,
  });
}

export function useProfiles(vendorId: string | null, includeInactive = false) {
  return useQuery({
    queryKey: keys.profiles(vendorId ?? "", includeInactive),
    queryFn: () =>
      api.get<{ items: ImportProfile[]; total: number }>(`/vendors/${vendorId}/import-profiles`, {
        include_inactive: includeInactive,
      }),
    enabled: vendorId !== null,
  });
}

export function useRuleSchemas() {
  return useQuery({
    queryKey: keys.ruleSchemas,
    queryFn: () => api.get<RuleSchemas>("/import-profiles/rule-schemas"),
    staleTime: Infinity,
  });
}

export function useImports(query?: Query) {
  return useQuery({ queryKey: keys.imports(query), queryFn: () => api.get<Page<ImportJob>>("/imports", query) });
}

export function useImportJob(id: string | null) {
  return useQuery({
    queryKey: keys.importJob(id ?? ""),
    queryFn: () => api.get<ImportJob>(`/imports/${id}`),
    enabled: id !== null,
  });
}

export function useImportReport(id: string | null) {
  return useQuery({
    queryKey: keys.importReport(id ?? ""),
    queryFn: () => api.get<ImportReport>(`/imports/${id}/report`),
    enabled: id !== null,
  });
}

export function useImportRows(id: string | null, query?: Query) {
  return useQuery({
    queryKey: keys.importRows(id ?? "", query),
    queryFn: () => api.get<Page<ImportRow>>(`/imports/${id}/rows`, query),
    enabled: id !== null,
  });
}

export function useExceptions(query?: Query) {
  return useQuery({
    queryKey: keys.exceptions(query),
    queryFn: () => api.get<Page<MappingException>>("/exceptions", query),
  });
}

export function useException(id: string | null) {
  return useQuery({
    queryKey: keys.exception(id ?? ""),
    queryFn: () => api.get<MappingExceptionDetail>(`/exceptions/${id}`),
    enabled: id !== null,
  });
}

export function useWatchlist(query?: Query) {
  return useQuery({
    queryKey: keys.watchlist(query),
    queryFn: () => api.get<Page<WatchlistEntry>>("/watchlist", query),
  });
}

export function useWatchHistory(id: string | null) {
  return useQuery({
    queryKey: keys.watchHistory(id ?? ""),
    queryFn: () => api.get<{ entry: WatchlistEntry; history: StatusHistory[] }>(`/watchlist/${id}/history`),
    enabled: id !== null,
  });
}

export function useEvents(query?: Query) {
  return useQuery({
    queryKey: keys.events(query),
    queryFn: () => api.get<Page<AvailabilityEvent>>("/availability/events", query),
  });
}

export function useAudit(query?: Query) {
  return useQuery({ queryKey: keys.audit(query), queryFn: () => api.get<AuditPage>("/audit/events", query) });
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => api.get<Dashboard>("/dashboard"),
    refetchInterval: 30_000,
  });
}
