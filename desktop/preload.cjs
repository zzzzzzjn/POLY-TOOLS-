const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("polyTools", {
  selectFiles: () => ipcRenderer.invoke("select-files"),
  selectOutputDirectory: () => ipcRenderer.invoke("select-directory"),
  inspect: (paths, container) => ipcRenderer.invoke("inspect", paths, container),
  validateOutput: (directory, name, paths) => ipcRenderer.invoke("validate-output", directory, name, paths),
  startMerge: (request) => ipcRenderer.invoke("start-merge", request),
  cancel: (jobId) => ipcRenderer.invoke("cancel", jobId),
  showInFolder: (path) => ipcRenderer.invoke("show-in-folder", path),
  pathForFile: (file) => webUtils.getPathForFile(file),
  subscribe: (listener) => {
    const handler = (_event, data) => listener(data);
    ipcRenderer.on("job-event", handler);
    return () => ipcRenderer.removeListener("job-event", handler);
  },
});
