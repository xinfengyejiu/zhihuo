"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { readerNovelUrl } from "@/lib/api";
import { Spinner } from "@/components/ui";

/** 旧路径兼容:跳转到独立阅读器 :3001 */
export default function ReaderRedirectCatalog() {
  const { novelId } = useParams<{ novelId: string }>();
  useEffect(() => {
    window.location.replace(readerNovelUrl(novelId));
  }, [novelId]);
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <Spinner text="正在打开独立阅读器…" />
    </div>
  );
}
