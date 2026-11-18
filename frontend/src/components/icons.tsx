/**
 * Inline SVG icon set.
 *
 * Hand-rolled rather than pulled from a library: nine navigation icons and a
 * handful of status glyphs do not justify a dependency, and inlining means they
 * inherit `currentColor` and need no runtime.
 *
 * All icons share a 24x24 viewBox and 1.75 stroke width so they sit together
 * evenly at any size.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function OverviewIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </Icon>
  );
}

export function VendorsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 21h18" />
      <path d="M5 21V7l7-4 7 4v14" />
      <path d="M9 21v-5h6v5" />
      <path d="M9 10h.01M15 10h.01" />
    </Icon>
  );
}

export function ProfilesIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h10M18 7h2M4 12h4M12 12h8M4 17h8M16 17h4" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="14" cy="17" r="2" />
    </Icon>
  );
}

export function ImportsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M12 3v13" />
      <path d="m7 8 5-5 5 5" />
    </Icon>
  );
}

export function ExceptionsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </Icon>
  );
}

export function ProductsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m21 8-9-5-9 5v8l9 5 9-5Z" />
      <path d="m3 8 9 5 9-5" />
      <path d="M12 13v8" />
    </Icon>
  );
}

export function WatchlistIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </Icon>
  );
}

export function AvailabilityIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 13h4l3 7 4-16 3 9h4" />
    </Icon>
  );
}

export function AuditIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3 4 6v6c0 4.5 3.2 8.4 8 9.5 4.8-1.1 8-5 8-9.5V6Z" />
      <path d="m9 12 2 2 4-4" />
    </Icon>
  );
}

export function DatabaseIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </Icon>
  );
}

export function PlugIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 2v6M15 2v6" />
      <path d="M6 8h12v3a6 6 0 0 1-12 0Z" />
      <path d="M12 17v5" />
    </Icon>
  );
}

export function MatchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10 7H7a5 5 0 0 0 0 10h3" />
      <path d="M14 7h3a5 5 0 0 1 0 10h-3" />
      <path d="M8 12h8" />
    </Icon>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Icon>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </Icon>
  );
}

export function ArrowRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Icon>
  );
}

export function ClockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Icon>
  );
}

/** Maps a nav href to its icon, so the shell stays declarative. */
export const NAV_ICONS: Record<string, (props: IconProps) => React.ReactElement> = {
  "/": OverviewIcon,
  "/vendors": VendorsIcon,
  "/profiles": ProfilesIcon,
  "/imports": ImportsIcon,
  "/financial-imports": ImportsIcon,
  "/purchasing-policies": ImportsIcon,
  "/exceptions": ExceptionsIcon,
  "/products": ProductsIcon,
  "/vendor-comparisons": VendorsIcon,
  "/replenishment-review": WatchlistIcon,
  "/inbound-evidence": ClockIcon,
  "/replenishment-now": ClockIcon,
  "/replenishment-recommendations": WatchlistIcon,
  "/watchlist": WatchlistIcon,
  "/availability": AvailabilityIcon,
  "/audit": AuditIcon,
};
