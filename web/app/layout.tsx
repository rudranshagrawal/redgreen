import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RedGreen Leaderboard",
  description:
    "The IDE catches its own bugs and learns which model to trust. Live leaderboard of 4-model bug-fix tournaments.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
