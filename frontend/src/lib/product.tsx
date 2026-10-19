"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { api } from "./api";
import { useAuth } from "./auth";

export type ProductKey = "novel" | "media" | string;

export interface ProductInfo {
  key: ProductKey;
  name: string;
  description: string;
  href: string;
  enabled: boolean;
}

interface ProductContextValue {
  products: ProductInfo[];
  current: ProductKey | null;
  setCurrent: (key: ProductKey) => void;
  loading: boolean;
}

const ProductContext = createContext<ProductContextValue>({
  products: [],
  current: null,
  setCurrent: () => {},
  loading: true,
});

const STORAGE_KEY = "xiaohuo_product";

function detectProduct(pathname: string | null): ProductKey | null {
  if (!pathname) return null;
  if (pathname.startsWith("/media")) return "media";
  if (pathname.startsWith("/drama")) return "drama";
  if (
    pathname.startsWith("/shelf") ||
    pathname.startsWith("/novels") ||
    pathname.startsWith("/review") ||
    pathname.startsWith("/publish") ||
    pathname.startsWith("/reader") ||
    pathname === "/"
  ) {
    return "novel";
  }
  // 共享区(/hub /me /profile /knowledge /skills)沿用已选产品
  return null;
}

export function ProductProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const pathname = usePathname();
  const [products, setProducts] = useState<ProductInfo[]>([]);
  const [current, setCurrentState] = useState<ProductKey | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) {
      setProducts([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const hub = await api.get<{ products: ProductInfo[]; last_product_key: string | null }>("/hub");
        if (cancelled) return;
        setProducts(hub.products);
        const stored = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
        const detected = detectProduct(pathname);
        setCurrentState(detected || stored || hub.last_product_key || "novel");
      } catch {
        setProducts([
          {
            key: "novel",
            name: "小说创作",
            description: "长篇网文创作",
            href: "/shelf",
            enabled: true,
          },
          {
            key: "media",
            name: "自媒体写作",
            description: "选题与成稿",
            href: "/media/chat",
            enabled: true,
          },
          {
            key: "drama",
            name: "漫剧创作",
            description: "剧本分镜与首帧",
            href: "/drama/chat",
            enabled: true,
          },
        ]);
        setCurrentState(detectProduct(pathname) || "novel");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => {
    const detected = detectProduct(pathname);
    if (detected) setCurrentState(detected);
  }, [pathname]);

  const setCurrent = useCallback((key: ProductKey) => {
    setCurrentState(key);
    if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, key);
    api.put("/hub/last-product", { product_key: key }).catch(() => {});
  }, []);

  const value = useMemo(
    () => ({ products, current, setCurrent, loading }),
    [products, current, setCurrent, loading]
  );

  return <ProductContext.Provider value={value}>{children}</ProductContext.Provider>;
}

export function useProduct() {
  return useContext(ProductContext);
}

/** 技能工坊列表:当前产品 + shared(无标签视为 shared) */
export function skillMatchesWorkshop(scopeTags: string[] | undefined | null, product: ProductKey | null): boolean {
  if (!product) return true;
  const tags = scopeTags?.length ? scopeTags : ["shared"];
  return tags.includes(product) || tags.includes("shared");
}

/**
 * 对话选用:只能选当前产品可用的。
 * - 带当前产品标签 → 可选
 * - 纯 shared → 可选
 * - 仅其它产品 / shared+其它产品且无当前产品 → 不可选
 */
export function skillMatchesChat(scopeTags: string[] | undefined | null, product: ProductKey | null): boolean {
  if (!product) return true;
  const tags = scopeTags?.length ? scopeTags : ["shared"];
  if (tags.includes(product)) return true;
  const foreign = tags.filter((t) => t !== "shared" && t !== product);
  if (foreign.length > 0) return false;
  return tags.includes("shared");
}

export function filterSkillsForWorkshop<T extends { scope_tags?: string[] | null }>(
  skills: T[],
  product: ProductKey | null
): T[] {
  return skills.filter((s) => skillMatchesWorkshop(s.scope_tags, product));
}

export function filterSkillsForChat<T extends { scope_tags?: string[] | null }>(
  skills: T[],
  product: ProductKey | null
): T[] {
  return skills.filter((s) => skillMatchesChat(s.scope_tags, product));
}

/** 知识库列表:当前产品 + shared(与技能工坊一致) */
export function kbMatchesWorkshop(scopeTags: string[] | undefined | null, product: ProductKey | null): boolean {
  return skillMatchesWorkshop(scopeTags, product);
}

export function filterKbsForWorkshop<T extends { scope_tags?: string[] | null }>(
  kbs: T[],
  product: ProductKey | null
): T[] {
  return kbs.filter((k) => kbMatchesWorkshop(k.scope_tags, product));
}
