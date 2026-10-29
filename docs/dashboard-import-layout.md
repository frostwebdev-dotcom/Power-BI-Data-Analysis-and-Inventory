# Dashboard import summary layout

## Problem and scope

The dashboard auto-fit grid can place three cards side by side. The five-column
last-import table then compresses its timestamps into several short lines and
can spill into the neighbouring synchronisation card. This is a presentation
fix within the Milestone 1 administration interface.

## Implementation plan

- Place the import summary first, across the full dashboard grid width.
- Keep the other dashboard cards in two columns, stacking on narrow screens.
- Display the import date and local time on separate, non-wrapping lines,
  retaining the year, seconds, and named timezone.
- Put the import table in a keyboard-accessible horizontal scroll region so
  narrow screens retain readable columns without overflowing the page.
- Show a vendor's name once, with its code underneath only when different.

## Acceptance and validation

All five import columns and report links remain available. Dates and times
stay within the import card, including when the viewport is narrow. Empty
and never-imported states retain their existing meaning. Data ingestion,
matching, authentication, and stored timestamps are unchanged.

Validate with frontend lint, TypeScript and production build checks, existing
dashboard browser coverage, and a visual inspection of the deployed dashboard.
