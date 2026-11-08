import type { Adapter } from "./types";

const unavailable = () => Promise.reject(new Error("Desktop access is unavailable in this browser preview. Open the desktop app to process audio."));
export const isDemo = !window.polyTools;
export const adapter: Adapter = window.polyTools ?? {
  selectFiles: unavailable,
  selectOutputDirectory: unavailable,
  inspect: unavailable,
  validateOutput: unavailable,
  startMerge: unavailable,
  cancel: unavailable,
  showInFolder: unavailable,
  pathForFile: () => "",
  subscribe: () => () => {},
};
