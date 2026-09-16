"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";

import { EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate, fmtNumber } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useImportJob, useImportReport, useImportRows, useImports, useProfiles, useVendors } from "@/lib/queries";
import type { ImportJob, ImportUpload } from "@/lib/types";

export default function ImportsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Imports />
    </Suspense>
  );
}

function Imports() {
  const params = useSearchParams();
  const selected = params.get("job");
  return selected ? <JobDetail jobId={selected} /> : <JobList />;
}

function JobList() {
  const { hasRole } = useAuth();
  const canUpload = hasRole("DATA_OPERATOR");
  const [page, setPage] = useState(1);
  const [vendorId, setVendorId] = useState("");
  const [status, setStatus] = useState("");
  const vendors = useVendors({ page_size: 200 });
  const jobs = useImports({ page, page_size: 25, vendor_id: vendorId, status });
  const vendorName = (id: string) => vendors.data?.items.find((v) => v.id === id)?.code ?? id.slice(0, 8);

  return (
    <>
      <PageHeader title="Imports" lede="Upload a vendor file; it is kept byte for byte, parsed through the vendor's profile, matched, and diffed against the last snapshot." />
      {canUpload ? <UploadCard /> : null}
      <div className="toolbar">
        <select value={vendorId} onChange={(e) => { setVendorId(e.target.value); setPage(1); }} aria-label="Vendor">
          <option value="">All vendors</option>
          {(vendors.data?.items ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.code} — {v.name}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
          <option value="">Any status</option>
          {["PENDING", "RUNNING", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].map((s) => (
            <option key={s} value={s}>
              {s.replaceAll("_", " ").toLowerCase()}
            </option>
          ))}
        </select>
      </div>
      <ErrorNote error={jobs.error} />
      {jobs.isPending ? <Loading what="Loading imports" /> : null}
      {jobs.data && jobs.data.items.length === 0 ? <EmptyState title="No imports yet" /> : null}
      {jobs.data && jobs.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="import-jobs">
            <thead>
              <tr>
                <th>File</th>
                <th>Vendor</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Rows</th>
                <th>Matched</th>
                <th>Exceptions</th>
                <th>Errors</th>
                <th>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((job) => (
                <tr key={job.id}>
                  <td>
                    <Link href={`/imports?job=${job.id}`}>{job.import_file.original_filename}</Link>
                  </td>
                  <td className="mono">{vendorName(job.vendor_id)}</td>
                  <td>
                    <StatusBadge value={job.status} />
                  </td>
                  <td>{job.current_stage?.toLowerCase() ?? "—"}</td>
                  <td>{fmtNumber(job.total_rows)}</td>
                  <td>{fmtNumber(job.matched_rows)}</td>
                  <td>{fmtNumber(job.exception_rows)}</td>
                  <td>{fmtNumber(job.error_rows)}</td>
                  <td>{fmtDate(job.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={jobs.data.page} pageSize={jobs.data.page_size} total={jobs.data.total} onPage={setPage} />
        </div>
      ) : null}
    </>
  );
}

function UploadCard() {
  const queryClient = useQueryClient();
  const vendors = useVendors({ page_size: 200 });
  const [vendorId, setVendorId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<ImportUpload | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const profiles = useProfiles(vendorId || null);

  useEffect(() => {
    if (!vendorId && vendors.data?.items[0]) setVendorId(vendors.data.items[0].id);
  }, [vendorId, vendors.data]);
  useEffect(() => {
    const first = profiles.data?.items[0];
    if (first && !profiles.data?.items.some((p) => p.id === profileId)) setProfileId(first.id);
  }, [profiles.data, profileId]);

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file.");
      const form = new FormData();
      form.append("vendor_id", vendorId);
      if (profileId) form.append("profile_id", profileId);
      form.append("file", file);
      return api.upload<ImportUpload>("/imports", form);
    },
    onSuccess: async (body) => {
      setResult(body);
      setFile(null);
      await queryClient.invalidateQueries({ queryKey: ["imports"] });
      await queryClient.invalidateQueries({ queryKey: ["exceptions"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  }

  return (
    <section className="card upload" data-testid="upload-card">
      <div className="grid grid--2">
        <label className="field">
          <span className="field__label">Vendor</span>
          <select value={vendorId} onChange={(e) => setVendorId(e.target.value)} data-testid="upload-vendor">
            {(vendors.data?.items ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.code} — {v.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Import profile</span>
          <select value={profileId} onChange={(e) => setProfileId(e.target.value)} data-testid="upload-profile">
            {(profiles.data?.items ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} v{p.version}
              </option>
            ))}
            {profiles.data && profiles.data.items.length === 0 ? <option value="">No active profile — the file will wait as PENDING</option> : null}
          </select>
        </label>
      </div>
      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        data-testid="dropzone"
      >
        <input ref={inputRef} type="file" accept=".csv,.xlsx" hidden data-testid="upload-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {file ? (
          <span>
            <strong>{file.name}</strong> · {(file.size / 1024).toFixed(1)} KB
          </span>
        ) : (
          <span>Drop a .csv or .xlsx here, or click to choose</span>
        )}
      </div>
      <div className="form__actions">
        <button type="button" className="button button--primary" disabled={!file || !vendorId || upload.isPending} onClick={() => upload.mutate()} data-testid="upload-submit">
          {upload.isPending ? "Uploading and processing…" : "Upload"}
        </button>
        <ErrorNote error={upload.error} />
      </div>
      {result ? (
        <Note tone={result.duplicate ? "warn" : "ok"}>
          {result.duplicate ? "These exact bytes were already received; showing the existing import. " : "Received. "}
          <Link href={`/imports?job=${result.job.id}`} data-testid="upload-result-link">
            {result.job.import_file.original_filename}
          </Link>{" "}
          — <StatusBadge value={result.job.status} /> {fmtNumber(result.job.matched_rows)} matched, {fmtNumber(result.job.exception_rows)} to the queue.
        </Note>
      ) : null}
    </section>
  );
}

function JobDetail({ jobId }: { jobId: string }) {
  const job = useImportJob(jobId);
  const report = useImportReport(jobId);
  const [rowStatus, setRowStatus] = useState("");
  const [rowPage, setRowPage] = useState(1);
  const rows = useImportRows(jobId, { status: rowStatus, page: rowPage, page_size: 50 });
  const [downloadError, setDownloadError] = useState<unknown>(null);

  async function download() {
    try {
      const { blob, filename } = await api.blob(`/imports/${jobId}/raw`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename ?? job.data?.import_file.original_filename ?? "file";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error);
    }
  }

  if (job.isPending) return <Loading what="Loading import" />;
  if (job.error || !job.data) return <ErrorNote error={job.error ?? new Error("Not found")} />;
  const j: ImportJob = job.data;

  return (
    <>
      <PageHeader
        title={j.import_file.original_filename}
        lede={report.data?.summary}
        actions={
          <>
            <Link href="/imports" className="button button--ghost">
              All imports
            </Link>
            <button type="button" className="button" onClick={download} data-testid="download-raw">
              Download raw file
            </button>
          </>
        }
      />
      <ErrorNote error={downloadError} />
      <div className="grid grid--2">
        <section className="card">
          <h2 className="card__title">Job</h2>
          <KeyValues
            rows={[
              ["Status", <StatusBadge key="s" value={j.status} />],
              ["Stage", j.current_stage?.toLowerCase() ?? "—"],
              ["Profile version", j.profile_version ? `v${j.profile_version}` : "none"],
              ["Uploaded", fmtDate(j.import_file.uploaded_at)],
              ["Completed", fmtDate(j.completed_at)],
              ["Size", `${fmtNumber(j.import_file.size_bytes)} bytes`],
              ["Encoding", j.import_file.detected_encoding ?? "—"],
              ["SHA-256", <span key="h" className="mono small">{j.import_file.sha256}</span>],
            ]}
          />
          {j.error_message ? <Note tone="warn">{j.error_message}</Note> : null}
        </section>
        <section className="card" data-testid="import-report">
          <h2 className="card__title">Report</h2>
          {report.data ? (
            <>
              <div className="counters">
                {Object.entries(report.data.counters).map(([key, value]) => (
                  <div className="counter" key={key}>
                    <span className="counter__value">{fmtNumber(value)}</span>
                    <span className="counter__label">{key}</span>
                  </div>
                ))}
              </div>
              {Object.keys(report.data.issues_by_code).length > 0 ? (
                <KeyValues rows={Object.entries(report.data.issues_by_code).map(([code, count]) => [code, fmtNumber(count)])} />
              ) : (
                <p className="muted">No row issues.</p>
              )}
            </>
          ) : (
            <Loading what="Loading report" />
          )}
        </section>
      </div>

      <section className="card">
        <div className="card__header">
          <h2 className="card__title">Rows</h2>
          <select value={rowStatus} onChange={(e) => { setRowStatus(e.target.value); setRowPage(1); }} aria-label="Row status" data-testid="row-status-filter">
            <option value="">All rows</option>
            {["OK", "WARNING", "ERROR", "SKIPPED"].map((s) => (
              <option key={s} value={s}>
                {s.toLowerCase()}
              </option>
            ))}
          </select>
        </div>
        <ErrorNote error={rows.error} />
        {rows.data ? (
          <div className="table-wrap">
            <table className="table table--compact" data-testid="import-rows">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Status</th>
                  <th>Vendor SKU</th>
                  <th>UPC</th>
                  <th>Description</th>
                  <th>Qty</th>
                  <th>Cost</th>
                  <th>Availability</th>
                  <th>Match</th>
                  <th>Issue</th>
                </tr>
              </thead>
              <tbody>
                {rows.data.items.map((row) => {
                  const match = (row.normalized_data.match ?? null) as { result?: string; method?: string; priority?: number } | null;
                  return (
                    <tr key={row.id}>
                      <td>{row.row_number}</td>
                      <td>
                        <StatusBadge value={row.status} />
                      </td>
                      <td className="mono">{row.vendor_sku ?? "—"}</td>
                      <td className="mono">{row.normalized_upc ?? "—"}</td>
                      <td>{row.description ?? "—"}</td>
                      <td>{fmtNumber(row.quantity)}</td>
                      <td>{fmtNumber(row.unit_cost)}</td>
                      <td>
                        <StatusBadge value={row.availability_status} />
                      </td>
                      <td>
                        {match?.result ? (
                          <>
                            <StatusBadge value={match.result} />
                            {match.method ? (
                              <span className="muted small">
                                {" "}
                                {match.method.replaceAll("_", " ").toLowerCase()} (p{match.priority})
                              </span>
                            ) : null}
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{row.error_code ? `${row.error_code}: ${row.error_message ?? ""}` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <Pager page={rows.data.page} pageSize={rows.data.page_size} total={rows.data.total} onPage={setRowPage} />
          </div>
        ) : (
          <Loading what="Loading rows" />
        )}
      </section>
    </>
  );
}
