"use client";

import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState, ErrorNote, Loading, Pager } from "@/components/ui";
import { useProduct, useProducts } from "@/lib/queries";

/** Search is a lookup; a reviewer must explicitly choose a result. */
export function ProductPicker({ value, onChange, disabled = false }: {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const inputId = useId();
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const chosen = useProduct(value || null);
  const results = useProducts({ q: search, page, page_size: 10 }, search !== null);
  function find() { setSearch(draft.trim()); setPage(1); }

  return (
    <div className="field" data-testid="product-picker">
      <label className="field__label" htmlFor={inputId}>Find a product</label>
      <div className="toolbar">
        <input id={inputId} value={draft} disabled={disabled} placeholder="Name, catalog number or UPC"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); find(); } }} />
        <button type="button" className="button" disabled={disabled} onClick={find}>Search products</button>
      </div>
      <ErrorNote error={chosen.error ?? results.error} />
      {value && chosen.isPending ? <Loading what="Loading selected product" /> : null}
      {chosen.data ? (
        <div className="note" data-testid="selected-product">
          Selected: <strong>{chosen.data.name}</strong> · {chosen.data.catalog_item_number}
          {!chosen.data.is_active ? " · inactive" : ""}{" · "}
          <Link href={`/products?product_id=${value}`} target="_blank" rel="noopener noreferrer">Details</Link>{" "}
          <button type="button" className="button button--ghost" disabled={disabled} onClick={() => onChange("")}>Clear selection</button>
        </div>
      ) : null}
      {search !== null && results.isPending ? <Loading what="Searching products" /> : null}
      {results.data ? (
        <>
          {results.data.items.length === 0 ? <EmptyState title="No products found" text="Try a name, catalog number or exact identifier." /> : null}
          {results.data.items.map((product) => (
            <div className="candidate" key={product.id}>
              <div><strong>{product.name}</strong> <span className="mono">{product.catalog_item_number}</span><div className="muted small">{product.brand ?? "No brand"}</div></div>
              <button type="button" className="button" disabled={disabled || product.id === value} onClick={() => onChange(product.id)}>Select</button>
            </div>
          ))}
          <Pager page={results.data.page} pageSize={results.data.page_size} total={results.data.total} onPage={setPage} />
        </>
      ) : null}
    </div>
  );
}
