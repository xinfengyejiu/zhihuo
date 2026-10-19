"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";

export interface TaskState {
  key: string;
  label: string;
  href?: string;
  status: "running" | "done" | "error";
  output: string;
  reasoning: string;
  meta: Record<string, unknown>;
  result?: unknown;
  error?: string;
  startedAt: number;
}

export interface TaskReporter {
  pushOutput: (text: string) => void;
  /** 用完整文本替换当前 output(用于配图占位被真实 URL 替换后刷新预览)。 */
  setOutput: (text: string) => void;
  pushReasoning: (text: string) => void;
  setMeta: (patch: Record<string, unknown>) => void;
  setResult: (result: unknown) => void;
}

export interface TaskConfig {
  key: string;
  label: string;
  href?: string;
  run: (reporter: TaskReporter) => Promise<unknown>;
}

interface TasksCtxValue {
  tasks: Record<string, TaskState>;
  runningCount: number;
  getTask: (key: string) => TaskState | undefined;
  startTask: (cfg: TaskConfig) => void;
  clearTask: (key: string) => void;
}

const Ctx = createContext<TasksCtxValue | null>(null);

/**
 * 全局 AI 任务中心:
 * - 任务在 Context 层运行,切换页面不中断、不丢状态;
 * - 同 key 任务运行中不允许重复发起;
 * - 不同 key 的任务天然并发。
 */
export function TaskProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, TaskState>>({});
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;

  const patch = useCallback((key: string, p: Partial<TaskState>) => {
    setTasks((prev) => {
      const cur = prev[key];
      if (!cur) return prev;
      return { ...prev, [key]: { ...cur, ...p } };
    });
  }, []);

  const startTask = useCallback(
    (cfg: TaskConfig) => {
      if (tasksRef.current[cfg.key]?.status === "running") return;
      const initial: TaskState = {
        key: cfg.key,
        label: cfg.label,
        href: cfg.href,
        status: "running",
        output: "",
        reasoning: "",
        meta: {},
        startedAt: Date.now(),
      };
      // 同步写入 ref,避免首包 SSE 早于 React setState 被丢掉
      tasksRef.current = { ...tasksRef.current, [cfg.key]: initial };
      setTasks((prev) => ({ ...prev, [cfg.key]: initial }));
      const reporter: TaskReporter = {
        pushOutput: (t: string) =>
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = { ...cur, output: cur.output + t };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          }),
        setOutput: (t: string) =>
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = { ...cur, output: t };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          }),
        pushReasoning: (t: string) =>
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = { ...cur, reasoning: cur.reasoning + t };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          }),
        setMeta: (p: Record<string, unknown>) =>
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = { ...cur, meta: { ...cur.meta, ...p } };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          }),
        setResult: (result: unknown) => {
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = { ...cur, result };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          });
        },
      };
      Promise.resolve()
        .then(() => cfg.run(reporter))
        .then((result) => {
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = {
              ...cur,
              status: "done" as const,
              ...(result === undefined ? {} : { result }),
            };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          });
        })
        .catch((e) => {
          setTasks((prev) => {
            const cur = prev[cfg.key] || tasksRef.current[cfg.key];
            if (!cur) return prev;
            const next = {
              ...cur,
              status: "error" as const,
              error: e instanceof Error ? e.message : "任务失败,请重试",
            };
            tasksRef.current = { ...tasksRef.current, [cfg.key]: next };
            return { ...prev, [cfg.key]: next };
          });
        });
    },
    []
  );

  const clearTask = useCallback((key: string) => {
    setTasks((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const getTask = useCallback((key: string) => tasks[key], [tasks]);
  const runningCount = Object.values(tasks).filter((t) => t.status === "running").length;

  return <Ctx.Provider value={{ tasks, runningCount, getTask, startTask, clearTask }}>{children}</Ctx.Provider>;
}

export function useTasks(): TasksCtxValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTasks 必须在 TaskProvider 内使用");
  return ctx;
}
