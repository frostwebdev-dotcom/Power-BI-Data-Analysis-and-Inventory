# Client vendor files — analysis and what it changes

Seven files received 16 September 2026. All five spreadsheets parsed in full; both PDFs read. Nothing was imported into the system — this is a read-only assessment.

---

## 1. What arrived

**Five vendor stock lists** (7,327 data rows, 6,246 distinct products):

| Vendor | File | Format | Rows | Columns provided |
|---|---|---|---|---|
| **NYB International Inc** | NYB Email with UPC 2026 3.xls | **.xls (Excel 97-2003)** | 727 | Qty on Hand, Item ID, UPC/SKU, Designer, Description, Net |
| **Perfume Center of America** | PCA INVENTORY 10.xls | **.xls (Excel 97-2003)** | 3,607 | DEPT, DESIGNER, UPC, CNTRYORGN, Item, Description, CASEPACK, Price A, Price A, Qty |
| **French Perfumes Intl Inc** | STOCK LIST 9-09-2026.xlsx | .xlsx | 893 | PARTICULARS, UPC, QTY, PRICE *(header on row 5)* |
| **YM Trading** | StockList.xlsx | .xlsx | 643 | Item Nu, Descripton *(sic)*, Pack Size, AVAIL, UPC, Unit Price |
| **TJS Group** | TJS GROUP PRICE LIST QTY 11.xlsx | .xlsx | 1,457 | Item, UPC, BRAND, Description, QTY, Price |

**Two PDFs — not inventory files.** `37968.pdf` is a sales invoice from French Perfumes International to Global Supplies NY Inc (USD 6,876, Net 30). `S00908584.pdf` is a sales order from Perfume Center of America to the same (24 lines, 813 units, customer PO PC9826). They are useful reference for Phase 4 purchase-order formatting and they confirm how each vendor identifies goods — French Perfumes quotes the UPC as the item number, PCA quotes its own item code (`I0090502`) — but they answer nothing that Phase 1 was waiting on.

Incidentally these files identify the business as **Global Supplies NY, Inc**, 138 31st St, Brooklyn NY, contact Sam Seidenfeld.

---

## 2. The good news

**The five files are genuinely usable, and four of them are well-formed.** Every file has a real UPC column, a real quantity column and a real price column. Check-digit validity is high: 709/727 in NYB, 3,471/3,607 in PCA, 834/893 in French Perfumes, 619/643 in YM Trading, 1,401/1,457 in TJS.

**There is real multi-vendor overlap, which is what the whole system depends on.** Across the four fragrance vendors, **616 products are carried by two or more vendors, 96 by three or more, 15 by all four.** Largest overlaps: PCA∩TJS 224 products, NYB∩PCA 161, PCA∩French Perfumes 151, NYB∩TJS 141. This means vendor comparison, best-price selection and "out of stock at A, now available at B" have live data to work with from day one, not a contrived demo.

**YM Trading is a different category** — Air Wick, Colgate, Crest, Febreze, Tide — and shares **zero** products with the four fragrance vendors. Worth confirming that this is deliberate and not a file sent by mistake.

---

## 3. Three findings that change the plan

### 3.1 Two of the five files are legacy `.xls`, which the system cannot read

NYB and PCA both send **Excel 97-2003 binary** workbooks, not `.xlsx`. The importer accepts CSV and XLSX only, and the library it uses (openpyxl) cannot open the old binary format at all. These are not small files to wave away: PCA alone is 3,607 rows, half the total data received.

Three ways forward, in order of preference:

1. **Add `.xls` support** — roughly half a day, using a converter in the ingestion path so the retained original stays byte-identical. Recommended, because vendors will not change their systems for us.
2. Ask both vendors to send `.xlsx` instead. Low cost to ask, low chance of success, and it breaks the moment someone at the vendor forgets.
3. Have the client re-save each file before uploading. Works, but it is manual work every week and it defeats the automated email intake planned for Phase 4.

I recommend option 1 and I will absorb it in Phase 1 rather than raise it as a change request.

### 3.2 French Perfumes has no vendor SKU column, and today that silently disables out-of-stock alerts for them

Their file is description, UPC, quantity, price — no vendor item number. The current code only creates a vendor inventory line when a vendor SKU is present, and the snapshot stage only looks at rows that have one. The practical effect: French Perfumes' 893 rows would match products correctly and then produce **no inventory snapshot, no availability event and no watchlist alert**. The vendor would look like it stocks nothing.

This was already on my fix list from last week's code review as a theoretical gap. These files prove it is a live one, for a vendor important enough that the client also sent us their invoice. The fix is to key the vendor line on the normalised UPC when no SKU is supplied. Half a day, already scheduled.

### 3.3 At this catalog ratio, unmatched rows would flood the exception queue

The client sells roughly 450 active products. The five vendors between them list **6,246 distinct products**. On a first import, PCA alone would produce somewhere near 3,200 rows that match nothing in the Nineyard catalog — not because anything is wrong, but because those are simply products the client does not sell. Today every one of those becomes a queue item for a human to resolve.

The brief already anticipates the distinction: section 33's example report separates "**25 new products**" from "**7 unmatched products**". They are different things and need to behave differently:

- **Not in our catalog, clean identifier** → record the vendor's stock and price, mark it as a product we do not carry, and leave it out of the queue. It becomes searchable supply data and feeds the Phase 5 new-product workflow.
- **Ambiguous, conflicting or broken identifier** → queue it, because a person genuinely must decide.

Without that split, the first real import produces thousands of queue items and the feature is unusable. With it, the queue holds tens of items, which is the intended experience. This is a small change to the matching step plus a new counter on the import report, and I would rather make it now, before the client sees their first import, than explain the number afterwards.

---

## 4. Data-quality issues that need a decision from the client

| # | Issue | Where | Scale | Proposed handling |
|---|---|---|---|---|
| 1 | **Quantity 999 as a sentinel** for "plenty in stock" rather than a literal count | French Perfumes 140 rows, YM Trading 83, TJS 41 | **264 rows** | Confirm the meaning. If it means unlimited, treat 999 as "available, quantity unknown" — otherwise the replenishment engine will cap purchase recommendations at 999 units and quietly under-order. |
| 2 | **Several UPCs in one cell, two different meanings** | YM Trading 24 rows, TJS 13 rows | 37 rows | TJS uses `UPC-A / UPC-B` for the same item in two packagings → map both barcodes to one product. YM Trading uses `UPC(6)UPC(6)UPC(3)` for display cases containing a mix of products → this is an assortment, a genuinely different concept. Needs a decision: treat the display as its own product, or explode it into components? |
| 3 | **Item number sitting in the UPC column** | PCA, 87 rows (`I0089006`) | 87 rows | These have no UPC at all, and a few are mistyped (`10083638` for `I0083638`). They will match on vendor SKU where one exists, otherwise land in the queue. Worth asking PCA whether a UPC exists for these lines. |
| 4 | **Duplicate UPCs within a single file** | PCA 91, YM Trading 17, TJS 8, NYB 2 | 118 rows | Same barcode on more than one line, usually different pack sizes or tester versions. Rule needed: keep the lowest price, the highest quantity, or sum them? My default is to keep the last valid occurrence and flag the row, but the client should pick. |
| 5 | **Failed check digits** | 37 rows across all five files | 37 rows | Imported with a warning, matched by vendor SKU where possible, never silently treated as a valid barcode. |
| 6 | **Short codes that are not UPCs** (3–8 digits) | PCA and French Perfumes, ~90 rows | ~90 rows | Same treatment as above. |
| 7 | **Blank prices** | YM Trading, 5 rows | 5 rows | Import the quantity, leave cost null, flag the row. Never guess a cost. |
| 8 | **Quantity "C" instead of a number** | TJS, 1 row | 1 row | Row rejected with an error code, rest of the file imports. |
| 9 | **Cosmetic irregularities** | PCA has the column heading "Price A" twice with identical values, and department codes padded with trailing spaces; YM Trading heads a column "Descripton"; French Perfumes puts four title rows above the real header | — | All handled by the import profile — header row index, column mapping by position, whitespace normalisation. No client action needed; noting it so nobody is surprised by the profile configuration. |

---

## 5. What is still outstanding

The files answer one of the four blocking asks. These remain:

1. **Nineyard API login and the four field questions** — which field is the Catalog Item Number, how Items relate to SKUs, whether an "updated since" filter exists, how deleted items appear. Still the largest open item in Phase 1, and nothing in these files touches it.
2. **Vendor contact details** — name, email address(es), minimum order quantity or value, lead time and payment terms for each of the five vendors. The two PDFs give a little (French Perfumes: Net 30, customer pickup; PCA: advance wire, LTL) but not enough to create the vendor records properly.
3. **Sending cadence per vendor** — which of these five send daily and which weekly, and to which mailbox.
4. **Hosting and sign-in decisions**, the pack-size policy, the user list, and the acceptance session — all unchanged from the previous request.

Two new questions these files raise:

5. **Is YM Trading (household and personal-care goods) in scope for this system**, or was that file sent by mistake? It shares no products with the other four vendors and it changes assumptions about the catalog.
6. **Are these five the complete vendor list**, or a sample? The answer decides whether five import profiles is the finished configuration or the first tranche.

---

## 6. Recommended reply to the client

> Thank you — these are exactly what I needed, and they are good files. Five vendors, 7,327 rows, 6,246 distinct products, and the check digits are clean enough that matching will work well. One thing worth knowing straight away: 616 products are carried by two or more of your vendors and 96 by three or more, so the vendor-comparison and out-of-stock features will have real data to work with rather than a demonstration.
>
> Four things I need from you before I set up the import profiles:
>
> **1. Quantity 999.** French Perfumes, YM Trading and TJS all use 999 in 264 rows between them. Does that mean "plenty, we won't run out", or is it a real count? If it means plenty, I will treat it as "available, quantity unknown" — otherwise the system will later recommend orders capped at 999 units.
>
> **2. Multi-item cells.** TJS sometimes lists two barcodes for one product separated by a slash — I will treat those as the same product with two barcodes. YM Trading has 24 display cases that list several different products with counts, like `030772033395(6)030772033418(6)`. Do you want those treated as a single purchasable display, or broken out into the products inside?
>
> **3. Duplicate barcodes.** PCA has 91 lines where the same barcode appears more than once, usually a different pack size or a tester. Which line should win — lowest price, highest quantity, or should I flag them for you to decide?
>
> **4. PCA lines with no barcode.** 87 PCA lines carry their item number in the barcode column instead of a UPC. I will match those on PCA's item number, but it is worth asking PCA whether a barcode exists for them.
>
> Two quick confirmations: is YM Trading (Air Wick, Colgate, Tide) meant to be part of this system? It shares no products with your four fragrance vendors. And are these five your complete vendor list or a first sample?
>
> Two notes on my side, both already handled, no action needed from you. NYB and PCA send the older Excel format rather than .xlsx, so I am adding support for it rather than asking them to change. And French Perfumes' list has no item-number column, only barcodes, so I am adjusting how their stock is recorded — their file will work exactly like the others.
>
> The invoice and the order you sent are genuinely useful — I will use them as the template for the purchase orders the system generates in Phase 4.
>
> Still outstanding and now the only thing holding Phase 1: the Nineyard login and the four questions about your catalog data. Could you also send contact details, minimum order requirements and payment terms for each of the five vendors, and tell me which send daily and which weekly?
