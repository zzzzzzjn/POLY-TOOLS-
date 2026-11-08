export type Source = {
  id: string; path: string; name: string; candidate: number | null; segment: number | null;
  rate: number; bits: number; validBits: number; encoding: "pcm" | "float";
  frames: string; timeReference: string | null;
};
export type Inspection = {
  files: Source[]; errors: string[]; warnings: string[]; size: string | null; container: string | null;
};
export type Phase = "empty" | "inspecting" | "needs_review" | "ready" | "merging" |
  "verifying" | "cancelling" | "completed" | "failed" | "cancelled";
export type JobEvent = {
  jobId: string; phase: "merging" | "verifying" | "cancelling" | "cancelled" | "completed" | "failed";
  done?: string; total?: string | null; error?: string; output?: string; channels?: number; warning?: string;
};
export type Adapter = {
  selectFiles(): Promise<string[]>;
  selectOutputDirectory(): Promise<string | null>;
  inspect(paths: string[], container: string): Promise<Inspection>;
  validateOutput(directory: string, name: string, paths: string[]): Promise<string | null>;
  startMerge(request: { id: string; paths: string[]; output: string; container: string; alignment: boolean; mapping: boolean }): Promise<void>;
  cancel(id: string): Promise<void>;
  showInFolder(path: string): Promise<void>;
  pathForFile(file: File): string;
  subscribe(listener: (event: JobEvent) => void): () => void;
};
declare global {
  interface Window { polyTools?: Adapter }
}
