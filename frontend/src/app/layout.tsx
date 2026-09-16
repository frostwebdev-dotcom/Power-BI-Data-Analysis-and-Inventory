import type { Metadata } from "next";

import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/Providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "Purchasing & Replenishment Management System",
  description: "Vendor catalogue, inventory imports, and product matching.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
