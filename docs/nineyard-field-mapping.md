# Nineyard Field Mapping

Status: **`DOCUMENTED` from the vendor's published OpenAPI specification.**
Nothing is `CONFIRMED` yet — no probe run has been executed.
Source: `https://backyard.nineyard.com/swagger/v1/swagger.json`
(`Nineyard.Rest.Api` v2.0, OpenAPI 3.0.1, retrieved 2026-09-08, 208 KB, saved to
`storage/diagnostics/nineyard/swagger-v1.json`)
Last updated: 2026-09-08

---

## How to read this document

| Marker | Meaning |
|---|---|
| `DOCUMENTED` | Declared in Nineyard's own OpenAPI spec. Strong evidence of the *contract*. |
| `CONFIRMED` | Observed in a probe run against the live API. Evidence of *behaviour*. |
| `ABSENT` | Searched for across all 117 schemas and demonstrably not present. |
| `UNVERIFIED` | Neither documented nor observed. |

The distinction between the first two matters. A spec can drift from the running
API, it says nothing about which endpoints *this account* may read, and — as §9
shows — this one documents only `200` responses, so every error shape is still
unknown. **Do not treat `DOCUMENTED` as sufficient to build the sync against.**

---

## 1. Authentication — `DOCUMENTED`

```
POST /api/OAuth/UsernameToken
```

Request body — schema `Nineyard.Rest.Api.Controllers.OAuthController.Creds`:

| Field | Type | Notes |
|---|---|---|
| `email` | string, nullable | |
| `password` | string, nullable | |
| `companyId` | integer (int32) | Not nullable |

Response — schema `Mappers.Repositories.Token`:

| Field | Type | Notes |
|---|---|---|
| `accessToken` | string, **nullable** | Declared nullable, so a 200 with a null token is contractually possible |
| `expiresIn` | integer (int32) | Not nullable. Unit not stated — presumably seconds |
| `expires` | string, **date-time** | Not nullable |

All three fields the brief mentioned are real. Security scheme is
`http`/`bearer`, `bearerFormat: JWT`.

**Still `UNVERIFIED`:** the unit of `expiresIn`; whether `expires` is UTC or
local; actual token lifetime; refresh behaviour (no refresh endpoint exists).

---

## 2. A cross-cutting surprise: `Content-Type: text/plain`

Every response in the spec — including the token — declares
**`text/plain`**, not `application/json`, while carrying a JSON schema.

The probe client is unaffected: it calls `response.json()`, which parses the body
regardless of the declared type. But it means:

- the probe's "content type" finding will read `text/plain`, and that is correct
  rather than a fault;
- any future consumer that branches on `Content-Type` will mis-handle these
  responses.

---

## 3. Pagination — `DOCUMENTED`, and **inconsistent between endpoints**

This is the single most operationally awkward finding.

| Endpoint | Request params | Response metadata | Envelope |
|---|---|---|---|
| `GET /api/Items` | `Page`, `PerPage` | `totalRecords`, `totalPages` | object |
| `GET /api/Vendors` | `Page`, `PerPage` | `totalRecords`, `totalPages` | object |
| `GET /api/PurchaseOrders` | `Page`, `PerPage` | `totalRecords`, `totalPages` | object |
| `GET /api/Skus` | **`PageNumber` only** | **none** | **bare array** |

`/api/Skus` differs in all three respects. It offers no page size and returns no
total, so the only way to know a SKU page is the last one is to receive fewer
records than the (undocumented) page size — or an empty page.

**Live verification (2026-09-29):** `/api/Items` accepts `PerPage` values of
50, 100, and 200, but rejects 250 and 500 with HTTP 400. The synchronization
reader therefore uses 200. `Page=1` is the first page. `/api/Skus` returns 100
records for `PageNumber=1`; its maximum/default page size remains undocumented.

---

## 4. `GET /api/Items` → `products` — `DOCUMENTED`

Envelope `Nineyard.Rest.Api.Model.ItemMappingResposne` *(the misspelling is
theirs)*: `totalRecords`, `totalPages`, `itemMapping[]`.

Records are `Nineyard.Rest.Api.Model.ItemMapping`. Query filters: `Page`,
`PerPage`, `ItemId`, `QuickBooksItemId`, `IsSynced`.

| Nineyard field | Type | Maps to | Notes |
|---|---|---|---|
| `itemId` | int32 | `products.catalog_item_number` **(candidate)** | See §5 — this is a decision, not a mapping |
| `title` | string, nullable | `products.name` | |
| `itemName` | string, nullable | — | Relationship to `title` `UNVERIFIED` |
| `brand` | string, nullable | `products.brand` | |
| `model` | string, nullable | `products.attributes` | Possible MPN |
| `notes` | string, nullable | `products.description` | |
| `caseQty` | int32, nullable | `products.pack_size` | Relates to blocking question B3 |
| `qtyPerVendorUnit` | int32, nullable | `vendor_products.pack_size` | |
| `vendorUPC` | string, nullable | `product_identifiers` (`UPC`) | **Vendor-scoped** — see §6 |
| `deleteFlag` | boolean, nullable | `products.is_active` (inverted) | See §11 |
| `deletedDateTime` | date-time, nullable | — | |
| `vendorId` | int32 | `vendor_products.vendor_id` | |
| `vendorName` | string, nullable | — | Denormalised |
| `itemVendorId` | int32 | `vendor_products.id` **(candidate)** | Item↔vendor link |
| `vendorItemName` | string, nullable | `vendor_products.vendor_description` | |
| `leadDays` | int32, nullable | `vendors.default_lead_time_days` | Also on Vendors |
| `purchaseDays` | int32, nullable | — | No column |
| `price` | double, nullable | `vendor_inventory_snapshots.unit_cost` | |
| `avgPrice` | double, nullable | — | No column |
| `qtyOnHand` | int32 | inventory — see §8 | Not nullable |
| `inboundStock` | int32, nullable | inventory — see §8 | |
| `localstock` | int32, nullable | inventory — see §8 | Lowercase `s`, unlike its neighbours |
| `totalStock` | int32, nullable | inventory — see §8 | |
| `reservedWarehouses` | array of `ReservedWarehouseQty` | — | Per-warehouse reservations |
| `length` `height` `width` `weight` | double, nullable | `products.attributes` | |
| `imageUrl` | string, nullable | `products.attributes` | |
| `caseRoundingSetting` | string, nullable | `products.attributes` | Vocabulary `UNVERIFIED` |
| `itemQuickBooksMappingId`, `quickBooksItemId`, `quickBooksVendorId`, `isSynced`, `lastSyncedDateTime`, `deleteFromQuickBooks` | mixed | — | QuickBooks; out of Milestone 1 scope |

**Note the shape of this endpoint.** It is called *ItemMapping*, carries six
QuickBooks fields, and filters on `IsSynced` and `QuickBooksItemId`. It reads as
a QuickBooks-integration view of items rather than a plain catalog listing.
Whether it returns *all* items or only QuickBooks-relevant ones is
`UNVERIFIED` and is the **first thing a probe run must establish** — if it is
filtered, it is the wrong endpoint for a catalog sync.

---

## 5. The Catalog Item Number — `ABSENT`

**There is no `catalogItemNumber` field.** Searched across all 117 schemas: no
field of that name, and no `itemNumber`, `catalogNumber` or `partNumber` on any
of the four endpoint groups.

This directly affects CLAUDE.md §5, which makes the Nineyard Catalog Item Number
the primary business reference, and `products.catalog_item_number`, which is
uniquely constrained on that basis.

The only viable candidate is **`itemId`** (int32) — Nineyard's internal
surrogate key. Using it means:

- `products.catalog_item_number` stores the string form of an integer;
- it is stable only insofar as Nineyard never reissues an id;
- it is not a number a human would recognise from a catalog.

`itemName` and `model` are the alternatives, both `string, nullable` — and a
nullable field cannot serve as a unique business key.

**This needs a decision from the client, not a guess from us.** Ask Nineyard
directly: *"Is there a catalog or part number for an item, and is `itemId`
stable across time?"* Everything in the product-identity model waits on the
answer.

---

## 6. UPC — `DOCUMENTED` but vendor-scoped only

The only UPC on the four groups is **`ItemMapping.vendorUPC`** — a UPC *as that
vendor states it*, not a product-level identifier.

Other UPC/GTIN/ASIN fields exist in the spec, but all on endpoints outside our
read-only set:

| Field | Endpoint group | In our set? |
|---|---|---|
| `ItemMapping.vendorUPC` | Items | **Yes** |
| `ShipmentSkuDTO.upc` / `.gtin` / `.asin` | Shipping | No |
| `PickItemList.upc`, `PickSKUList.upc`/`.gtin`/`.asin`/`.fnSkuBarCode` | Shipping/Picking | No |
| `QBPoItemVendorDTO.upc` | PurchaseOrders (nested) | Partly |
| `ApiSkuCost.asin` | `Skus/GetCostOfSkusByDate` | No |
| `GetReplenishSkus.Result.asin` | `Skus/GetReplenish` | No |
| `SkuEdits.upc` | `POST /api/Skus` | **No — write endpoint** |

Consequences for match priority 1 (exact normalized UPC):

- a UPC arrives **scoped to a vendor**, so the same product reached through two
  vendors may present two different UPC values, or one and a blank;
- it is `nullable`, so coverage is unknown until a probe run measures it;
- there is no product-level UPC to treat as canonical.

The schema already supports this correctly: `product_identifiers` carries
`vendor_id` context, and the vendor-scoped partial unique index allows two
vendors to state different values. But the **canonical** UPC for a product has no
documented source.

---

## 7. `GET /api/Skus` → `marketplace_listings` — `DOCUMENTED`

**This is the Amazon side.** Bare array of `Mappers.ApiModels.ApiSku`. Query
filters: `PageNumber`, `Sku`, `Account`.

| Nineyard field | Type | Maps to | Notes |
|---|---|---|---|
| `sku` | string, nullable | `marketplace_listings.seller_sku` | |
| `accountSkuId` | int32 | `source_records.source_record_id` | The join key for `GetSkuMappings` |
| `id` | int32 | — | Relationship to `accountSkuId` `UNVERIFIED` |
| `channel` | string, nullable | `marketplace_listings.marketplace` | Vocabulary `UNVERIFIED` — must map onto our enum |
| `channelId` | string, nullable | `marketplace_listings.marketplace_id` | |
| `account` | string, nullable | — | Seller account. No column; multiple accounts per channel are plausible |
| `fulfillmentType`, `fbaType` | string, nullable | `marketplace_listings` attributes | FBA/FBM. No column yet |
| `isActive` | boolean | `marketplace_listings.is_active` | Not nullable |
| `title` | string, nullable | — | Listing title, distinct from the item title |
| `qty` | int32 | inventory — see §8 | |
| `inboundStock`, `reserve` | int32, nullable | inventory — see §8 | |
| `price`, `minPrice`, `maxPrice`, `defaultPrice`, `mapPrice` | double | — | Pricing; Milestone 1 has no destination |
| `cost`, `shipCost`, `prepCost`, `markup`, `minMarkup` | double | — | Profitability — explicitly out of Milestone 1 scope |
| `priceModel`, `priceModelName`, `isMinPriceManual`, `isMaxPriceManual`, `isMapActive` | mixed | — | Repricer configuration |
| `rank`, `category`, `image` | mixed | `attributes` | |

**`ASIN` is `ABSENT` from this endpoint.** It appears only on
`Skus/GetCostOfSkusByDate` and `Skus/GetReplenish`, neither of which is in the
read-only set under investigation. So `marketplace_listings.asin` has **no
source** from the four groups.

---

## 8. The Items ↔ Skus relationship — `DOCUMENTED`, and it is many-to-many

`GET /api/Skus/GetSkuMappings?AccountSkuIds=<array>` returns
`Mappers.ApiModels.ApiSkuMapping`:

```
{ "accountSkuId": int32,
  "mappedItems": [ { "itemId": int32, "name": string?, "qty": int32 } ] }
```

This answers the question the previous revision flagged as second-most
important, and the answer is more complex than assumed:

- a SKU maps to **many items**, not one;
- each mapping carries a **`qty`** — so a SKU can be a bundle, kit, or multi-pack
  of one or more items.

CLAUDE.md §5 states "one Catalog Item may have multiple Amazon SKUs", which
`marketplace_listings` models as one-to-many. **Nineyard's actual relationship is
many-to-many with a quantity**, which that table cannot represent: a bundle SKU
composed of two different items has no valid single `product_id`.

This is a genuine modelling gap, not a mapping detail. Options, for a decision
later and not now:

1. treat only 1:1 mappings as `marketplace_listings` and route bundles to the
   exception queue;
2. add a join table with quantity;
3. exclude bundle SKUs from Milestone 1 with the limitation documented.

Note also that this endpoint is **not** in the four groups and requires
`AccountSkuIds` — you must already have the SKUs before you can ask what they map
to. It is a second call, not part of the listing.

---

## 9. Inventory fields — `DOCUMENTED`

Milestone 1 records vendor inventory as append-only snapshots. Nineyard exposes
quantities in three places with **no documented definition of any of them**:

| Field | Endpoint | Type |
|---|---|---|
| `qtyOnHand` | Items | int32, not nullable |
| `localstock` | Items | int32, nullable |
| `inboundStock` | Items | int32, nullable |
| `totalStock` | Items | int32, nullable |
| `reservedWarehouses[]` | Items | array of `ReservedWarehouseQty` |
| `qty` | Skus | int32, not nullable |
| `inboundStock`, `reserve` | Skus | int32, nullable |
| `qty` per location | `Items/GetItemLocations` | int32 |

**`UNVERIFIED` and material:** whether `totalStock = qtyOnHand + inboundStock +
localstock`; whether reserved quantities are already deducted; which figure means
"available to promise". Availability detection (`AVAILABLE` vs `OUT_OF_STOCK`)
depends entirely on picking the right one, and picking wrong produces
availability events that are silently untrue.

Ask Nineyard for the definitions. This is not inferable from a probe run.

---

## 10. `GET /api/Vendors` → `vendors` — `DOCUMENTED`

Envelope `VendorMappingResponse`: `totalRecords`, `totalPages`,
`vendorMapping[]`. Records are `Nineyard.Rest.Api.Model.VendorMapping`.

| Nineyard field | Type | Maps to | Notes |
|---|---|---|---|
| `vendorId` | int32 | `source_records.source_record_id` | See below |
| `vendorName` | string, nullable | `vendors.name` | Nullable, though our column is NOT NULL |
| `email` | string, nullable | `vendors.contact_email` | |
| `phone` | string, nullable | `vendors.contact_phone` | |
| `leadDays` | int32, nullable | `vendors.default_lead_time_days` | |
| `purchaseDays` | int32, nullable | — | No column |
| `safetyStock` | int32, nullable | — | No column |
| `deleteFlag` | boolean | `vendors.is_active` (inverted) | Not nullable here |
| `deletedDateTime` | date-time, nullable | `vendors.deactivated_at` equivalent | |
| `addedOn` | date-time | — | Vendor creation time |
| `address1`, `address2`, `city`, `state`, `postal`, `country` | string, nullable | — | No columns; `vendor_contact` could hold these |
| `vendorQuickBooksMappingId`, `quickBooksVendorId`, `isSynced`, `lastSyncedDateTime`, `deleteFromQuickBooks` | mixed | — | QuickBooks; out of scope |

**`vendors.code` is `ABSENT`.** There is no vendor code, mnemonic or short name.
Our `vendors.code` is NOT NULL, uppercase-constrained and unique per
organization, so it must be **generated locally** — the Nineyard `vendorId`
belongs in `source_records`, not in `code`.

**`vendors.currency` is `ABSENT`.** No currency field on the vendor or on any
price field. Our column defaults to `USD`; that default becomes an assumption
that needs confirming with the client.

---

## 11. Deletion semantics — `DOCUMENTED`

Items and Vendors both carry `deleteFlag` (boolean) and `deletedDateTime`.
Skus carry `isActive` instead.

So deletion is **soft and flagged**, not absence from the collection — which
suits our soft-deactivation model well.

**`UNVERIFIED`:** whether deleted records are returned by default or must be
requested. If they are excluded by default, a sync would see them vanish and
must not interpret that as an error. A probe run answers this by comparing
`totalRecords` against the count of records with `deleteFlag = true`.

---

## 12. Available timestamps — `DOCUMENTED`

| Field | Endpoint | Meaning |
|---|---|---|
| `addedOn` | Vendors | Vendor created |
| `lastSyncedDateTime` | Items, Vendors | QuickBooks sync, **not** record modification |
| `deletedDateTime` | Items, Vendors | Soft-delete moment |
| `lastUpdate` | `Items/GetItemLocations` | Per-location stock update |
| `dateCreated`, `dateSubmited`, `expectedOn`, `recievedDate`, `invoiceDate`, `lastUpdated` | PurchaseOrders | *(`dateSubmited` and `recievedDate` are misspelled in the API)* |

**There is no general "last modified" timestamp on Items, Vendors or Skus.**
`lastSyncedDateTime` refers to QuickBooks and must not be repurposed as one.

**Timezone is `UNVERIFIED`** for every timestamp. Everything is stored as UTC
`TIMESTAMPTZ`, so a naive local timestamp needs a documented assumption about
which zone it is in.

---

## 13. Incremental sync — `ABSENT`

Every parameter on all 65 paths was searched for date, since, modified, updated,
from and to. The only matches are:

- `Skus/GetCostOfSkusByDate :: Date`
- `Shipping/GetLabel :: PackageLabelsToPrint`
- `Shipping :: WithBoxContentOnly`

**No `updatedSince` filter exists on any of the four groups.** Catalog
synchronisation must be a **full paginated pull** every time, with change
detection done locally by diffing against retained `source_records` payloads.

This settles the open question behind `nineyard_sync_runs.cursor`: unless
Nineyard adds a filter, that column stays unused. It is harmless, and now
knowingly so rather than hopefully so.

---

## 14. `GET /api/PurchaseOrders` — no Milestone 1 destination

Purchase-order generation is out of scope (CLAUDE.md §3) and there is no
purchase-order table. Recorded as reconnaissance only.

Envelope `PurchaseOrderMappingResponse`: `totalRecords`, `totalPages`,
`purchaseOrderMapping[]`. Records are `QBPoListResultDTO`:
`purchaseOrderId`, `vendorId`, `poName`, `dateCreated`, `dateSubmited`,
`status` (int32, nullable), `notes`, `itemsCost`, `shippingCost`,
`discountAmount`, `discountPct`, `expectedOn`, `subTotal`, `recievedDate`,
`isFullyRecieved`, `invoiceNumber`, `otherCost`, `invoiceDate`, `submitedBy`,
`lastUpdated`, plus nested `purchaseOrderItems`, `vendor`, `poStatus` and six
QuickBooks fields.

Useful corroboration: vendors are referenced by `vendorId` here too, consistent
with §10. `status` is an integer whose vocabulary is `UNVERIFIED`.

**Do not build a purchase-order table from this.**

---

## 15. Fields our client needs that Nineyard does not provide

The heart of the gap analysis.

| Our column | Status | Consequence |
|---|---|---|
| `products.catalog_item_number` | **No true source** | `itemId` is a surrogate key, not a catalog number. Needs a client decision (§5). |
| `product_identifiers` canonical UPC | **No product-level source** | Only `vendorUPC`, vendor-scoped and nullable. Match priority 1 will have partial coverage. |
| `marketplace_listings.asin` | **Not in the four groups** | Only on `GetReplenish` / `GetCostOfSkusByDate`. Match priority 4 has no data source (blocking question B7 answered: not from these endpoints). |
| `vendors.code` | **Absent** | Must be generated locally; `vendorId` goes to `source_records`. |
| `vendors.currency` | **Absent** | The `USD` default becomes an assumption to confirm. |
| `products.manufacturer` | **Absent** | `brand` exists; manufacturer does not. |
| `products.unit_of_measure` | **Absent** | `caseQty` and `qtyPerVendorUnit` exist as counts, but no UoM. |
| `vendors.timezone` | **Absent** | Expected — configure locally. |
| A record "last modified" timestamp | **Absent** | Forces full pulls (§13). |
| A 1:1 product↔listing relationship | **Contradicted** | Nineyard is many-to-many with quantity (§8). |

Conversely, Nineyard offers a great deal Milestone 1 has no home for: pricing and
repricer configuration, profitability inputs (`cost`, `shipCost`, `prepCost`,
`markup`), replenishment recommendations, warehouse locations, shipping and
box-packing, and pervasive QuickBooks mapping. All are out of scope, and several
belong to milestones explicitly excluded from this one.

---

## 16. What a probe run still has to establish

The spec cannot answer any of these:

- [ ] Which of the four groups this account may actually read (403s are findings)
- [ ] Whether `GET /api/Items` returns the full catalog or only QuickBooks-relevant items (§4) — **the highest-value question**
- [ ] Actual error shapes: only `200` is documented, so 401/403/404/429 bodies are unknown (§9 of the integration doc)
- [ ] Real `Content-Type` headers versus the declared `text/plain`
- [ ] Default and maximum page size; whether `Page` is 0- or 1-based
- [ ] `/api/Skus` page size, given it returns no metadata
- [ ] `vendorUPC` coverage — what proportion of items actually carry one
- [ ] Whether soft-deleted records appear by default
- [ ] Actual timestamp format and whether an offset is present
- [ ] Whether nullable fields are null in practice or merely declared so
- [ ] Rate limits — undocumented

And these need a person at Nineyard, not a probe:

- [ ] Is there a catalog/part number, and is `itemId` stable over time? (§5)
- [ ] Definitions of `qtyOnHand` / `localstock` / `inboundStock` / `totalStock`, and whether reservations are already deducted (§9)
- [ ] The `channel` vocabulary, and the `caseRoundingSetting` and PO `status` vocabularies
- [ ] Timezone of all timestamps
- [ ] Whether an `updatedSince` filter could be added (§13)

---

## 17. Before writing any mapping code

- [ ] A probe run completed and a sanitised sample saved
- [ ] §16's probe list answered
- [ ] §5 resolved — the Catalog Item Number decision made **with the client**
- [ ] §8 resolved — how bundle SKUs are handled
- [ ] §9 resolved — which quantity means "available"
- [ ] §15 accepted — the client agrees to the gaps, or supplies another source

Only then: the anti-corruption layer (ADR 0007), then the sync service. The spec
has removed most of the *discovery* risk; it has removed none of the
*correctness* risk in §5, §8 and §9, and those three decide whether the data is
right.
