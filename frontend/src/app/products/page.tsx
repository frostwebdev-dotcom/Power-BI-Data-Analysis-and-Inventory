"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Drawer, EmptyState, ErrorNote, KeyValues, Loading, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useProduct, useProductIdentifiers, useProductListings, useProducts } from "@/lib/queries";

export default function ProductsPage() {
  return <Suspense fallback={<Loading />}><Catalogue /></Suspense>;
}

function Catalogue() {
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("product_id"));
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [includeInactive, setIncludeInactive] = useState(false);
  const products = useProducts({ page, page_size: 25, q: query, include_inactive: includeInactive });

  return <>
    <PageHeader title="Products" lede="The Nineyard catalogue, its identifiers and mapped marketplace listings." />
    <form className="toolbar" onSubmit={(event) => { event.preventDefault(); setQuery(draft.trim()); setPage(1); }}>
      <input aria-label="Search catalogue" placeholder="Name, catalog number or UPC" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <button type="submit" className="button">Search</button>
      <label className="check"><input type="checkbox" checked={includeInactive} onChange={(event) => { setIncludeInactive(event.target.checked); setPage(1); }} />Show inactive</label>
    </form>
    <ErrorNote error={products.error} />
    {products.isPending ? <Loading what="Loading catalogue" /> : null}
    {products.data ? <>
      <p className="muted small">{products.data.total.toLocaleString()} products</p>
      {products.data.items.length === 0 ? <EmptyState title="No products found" text={query ? "Try another name, catalog number or exact identifier." : "Run the Nineyard catalogue sync to populate this organization."} /> : (
        <div className="table-wrap"><table className="table" data-testid="products">
          <thead><tr><th>Catalog #</th><th>Product</th><th>Brand</th><th>Status</th><th>Last seen in Nineyard</th><th /></tr></thead>
          <tbody>{products.data.items.map((product) => <tr key={product.id} data-testid={`product-row-${product.catalog_item_number}`}>
            <td className="mono">{product.catalog_item_number}</td><td>{product.name}</td><td>{product.brand ?? "—"}</td>
            <td><StatusBadge value={product.is_active ? product.status : "INACTIVE"} /></td><td>{fmtDate(product.nineyard_last_seen_at)}</td>
            <td><button type="button" className="button button--ghost" onClick={() => setSelected(product.id)}>Details</button></td>
          </tr>)}</tbody>
        </table></div>
      )}
      <Pager page={products.data.page} pageSize={products.data.page_size} total={products.data.total} onPage={setPage} />
    </> : null}
    {selected ? <ProductDrawer key={selected} id={selected} onClose={() => setSelected(null)} /> : null}
  </>;
}

function ProductDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { hasRole } = useAuth();
  const [identifierPage, setIdentifierPage] = useState(1);
  const [listingPage, setListingPage] = useState(1);
  // Independent reads start together; large identifier/listing collections stay bounded.
  const product = useProduct(id);
  const identifiers = useProductIdentifiers(id, { page: identifierPage, page_size: 25 });
  const listings = useProductListings(id, { page: listingPage, page_size: 25 });
  return <Drawer title={product.data?.name ?? "Product details"} onClose={onClose} wide>
    <ErrorNote error={product.error} />
    {product.isPending ? <Loading /> : null}
    {product.data ? <>
      <KeyValues rows={[
        ["Catalog number", product.data.catalog_item_number], ["Brand", product.data.brand ?? "—"],
        ["Manufacturer", product.data.manufacturer ?? "—"], ["Description", product.data.description ?? "—"],
        ["Pack size", product.data.pack_size ?? "—"], ["Unit", product.data.unit_of_measure ?? "—"],
        ["Status", <StatusBadge key="status" value={product.data.is_active ? product.data.status : "INACTIVE"} />],
        ["Last seen in Nineyard", fmtDate(product.data.nineyard_last_seen_at)],
      ]} />
      {hasRole("PURCHASING_MANAGER") && product.data.is_active ? <p><Link className="button" href={`/watchlist?product_id=${id}`}>Watch this product</Link></p> : null}
      <section className="card"><h3 className="card__title">Identifiers</h3>
        <ErrorNote error={identifiers.error} />
        {identifiers.isPending ? <Loading /> : null}
        {identifiers.data ? <>
          <p className="muted small">{identifiers.data.total} identifiers · includes retained inactive identifiers</p>
          <div className="table-wrap"><table className="table table--compact" data-testid="product-identifiers">
            <thead><tr><th>Type</th><th>Original value</th><th>Normalized value</th><th>Source</th><th>Context</th><th>Active</th></tr></thead>
            <tbody>{identifiers.data.items.map((item) => <tr key={item.id}>
              <td>{item.identifier_type}</td><td className="mono">{item.raw_value}</td><td className="mono">{item.normalized_value}</td><td>{item.source_system}</td>
              <td>{item.vendor_id ? <span className="mono small">Vendor {item.vendor_id}</span> : item.marketplace_listing_id ? <span className="mono small">Listing {item.marketplace_listing_id}</span> : "Catalogue"}{item.is_primary ? " · primary" : ""}</td><td>{item.is_active ? "Yes" : "No"}</td>
            </tr>)}</tbody>
          </table></div>
          {identifiers.data.total === 0 ? <EmptyState title="No identifiers" /> : null}
          <Pager page={identifiers.data.page} pageSize={identifiers.data.page_size} total={identifiers.data.total} onPage={setIdentifierPage} />
        </> : null}
      </section>
      <section className="card"><h3 className="card__title">Marketplace listings</h3>
        <ErrorNote error={listings.error} />
        {listings.isPending ? <Loading /> : null}
        {listings.data ? <>
          <p className="muted small">{listings.data.total} listings · includes inactive listings</p>
          <div className="table-wrap"><table className="table table--compact" data-testid="product-listings">
            <thead><tr><th>Marketplace</th><th>Seller SKU</th><th>ASIN</th><th>Title</th><th>Listing</th><th>Mapping</th><th>Approval</th></tr></thead>
            <tbody>{listings.data.items.map((item) => <tr key={item.id}>
              <td>{item.marketplace} · {item.marketplace_id}</td><td className="mono">{item.seller_sku}</td><td>{item.asin ?? "—"}</td><td>{item.name ?? "—"}</td>
              <td><StatusBadge value={item.is_active ? item.listing_status : "INACTIVE"} /></td><td><StatusBadge value={item.mapping_status} />{item.mapping_method ? ` · ${item.mapping_method}` : ""}</td>
              <td>{fmtDate(item.approved_at)}{item.approved_by_user_id ? <div className="mono small">By {item.approved_by_user_id}</div> : null}</td>
            </tr>)}</tbody>
          </table></div>
          {listings.data.total === 0 ? <EmptyState title="No mapped listings" /> : null}
          <Pager page={listings.data.page} pageSize={listings.data.page_size} total={listings.data.total} onPage={setListingPage} />
        </> : null}
      </section>
    </> : null}
  </Drawer>;
}
