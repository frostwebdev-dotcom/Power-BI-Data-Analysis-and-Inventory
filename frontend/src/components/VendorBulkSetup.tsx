"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Drawer, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";

interface RosterResult {
  ready: boolean;
  applied: boolean;
  created: number;
  existing: number;
  rows: {
    row_number: number;
    code: string;
    name: string;
    action: string;
    issues: string[];
  }[];
}

export function VendorBulkSetup({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [csv, setCsv] = useState("");
  const [fileError, setFileError] = useState<Error | null>(null);
  const [preview, setPreview] = useState<RosterResult | null>(null);
  const [reading, setReading] = useState(false);
  const setup = useMutation({
    mutationFn: (apply: boolean) =>
      api.post<RosterResult>("/vendors/bulk-setup", { csv_text: csv, apply }),
    onSuccess: async (result) => {
      setPreview(result);
      if (result.applied) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["vendors"] }),
          queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        ]);
      }
    },
  });
  const newCount = preview?.rows.filter((row) => row.action === "create").length ?? 0;

  return (
    <Drawer title="Set up vendors from a list" onClose={onClose}>
      <p>
        Upload the approved vendor list as a UTF-8 CSV, with one vendor per row.
        Code and name are required. Contact details, currency, lead time, minimum
        orders, payment and freight terms, and inventory cadence are optional.
      </p>
      <p>
        <a className="button button--ghost" href="/vendor-roster-template.csv" download>
          Download vendor template
        </a>
      </p>
      <p className="muted small">
        Existing vendors keep their saved settings. Use Edit to change an existing
        vendor. Inventory files and import profiles are configured separately from
        this list.
      </p>
      <form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          setup.mutate(false);
        }}
      >
        <label className="field">
          <span className="field__label">Vendor list CSV</span>
          <input
            type="file"
            accept=".csv,text/csv"
            disabled={setup.isPending || reading}
            onChange={async (event) => {
              const file = event.target.files?.[0];
              setCsv("");
              setPreview(null);
              setFileError(null);
              setup.reset();
              if (!file) return;
              if (file.size > 1_000_000) {
                setFileError(new Error("Choose a CSV under 1 MB, with at most 500 vendors."));
                return;
              }
              setReading(true);
              try {
                setCsv(await file.text());
              } catch {
                setFileError(new Error("The file could not be read. Please choose it again."));
              } finally {
                setReading(false);
              }
            }}
          />
        </label>
        <ErrorNote error={fileError ?? setup.error} />
        <button type="submit" className="button" disabled={!csv || reading || setup.isPending}>
          Preview vendor list
        </button>
      </form>
      {preview ? (
        <>
          <p role="status">
            {preview.applied
              ? preview.created + " vendors created; " + preview.existing + " existing vendors preserved."
              : preview.rows.length + " rows: " + newCount + " new vendors, " + preview.existing + " already configured."}
          </p>
          {!preview.ready ? (
            <p role="alert">Correct the invalid rows and upload the file again. No vendors were created.</p>
          ) : null}
          <div className="table-wrap">
            <table className="table table--compact" data-testid="vendor-roster-preview">
              <thead>
                <tr><th>Row</th><th>Code</th><th>Vendor</th><th>Result</th><th>Details</th></tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>{row.code}</td>
                    <td>{row.name}</td>
                    <td>{row.action === "create" ? preview.applied ? "Created" : "Will create" : row.action === "existing" ? "Already configured" : "Invalid"}</td>
                    <td>{row.issues.join(" ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            type="button"
            className="button button--primary"
            disabled={!preview.ready || preview.applied || newCount === 0 || setup.isPending}
            onClick={() => setup.mutate(true)}
          >
            Create {newCount} vendors
          </button>
        </>
      ) : null}
    </Drawer>
  );
}
