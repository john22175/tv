import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TV Sources",
  description: "Manage the media library downloaded by MultiHub TV receivers.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
