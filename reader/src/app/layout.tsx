import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { BottomNav } from "@/components/BottomNav";

export const metadata: Metadata = {
  title: "织火阅读器",
  description: "织火小说阅读 · 书城 · 书架",
  appleWebApp: {
    capable: true,
    title: "织火阅读",
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f3ea" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1c22" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-dvh antialiased">
        <AuthProvider>
          <div className="pb-16">{children}</div>
          <BottomNav />
        </AuthProvider>
      </body>
    </html>
  );
}
