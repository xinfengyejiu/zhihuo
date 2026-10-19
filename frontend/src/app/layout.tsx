import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ProductProvider } from "@/lib/product";
import { TaskProvider } from "@/lib/tasks";
import TopBar from "@/components/TopBar";
import { MainShell } from "@/components/MainShell";

export const metadata: Metadata = {
  title: "织火 · 多产品 AI 创作平台",
  description: "小说创作 · 自媒体写作 · 漫剧创作 · 模型与知识库共享",
  appleWebApp: {
    capable: true,
    title: "织火",
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8f4ea" },
    { media: "(prefers-color-scheme: dark)", color: "#1e2028" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-dvh antialiased">
        <AuthProvider>
          <ProductProvider>
            <TaskProvider>
              <TopBar />
              <MainShell>{children}</MainShell>
            </TaskProvider>
          </ProductProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
