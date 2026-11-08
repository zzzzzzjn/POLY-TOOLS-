import type { JobEvent, Phase } from "./types";

export type TaskState = {
  phase: Phase;
  jobId: string | null;
  done: string | null;
  total: string | null;
  error: string;
  output: string;
  channels: number;
};
export type TaskAction =
  | { type: "status"; phase: Phase; error?: string }
  | { type: "start"; jobId: string }
  | { type: "error"; error: string }
  | { type: "cancel" }
  | { type: "cancel_failed"; phase: "merging" | "verifying" }
  | { type: "event"; event: JobEvent };
export const initialTask: TaskState;
export function taskReducer(state: TaskState, action: TaskAction): TaskState;
