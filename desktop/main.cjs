const { app, BrowserWindow, dialog, ipcMain, shell, nativeImage } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const { existsSync } = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const root = path.join(__dirname, "..");
const python = process.env.POLY_TOOLS_PYTHON || "python3";
const backend = app.isPackaged ? path.join(process.resourcesPath, "backend", "poly-backend") : python;
function backendArgs(command, args = []) {
  if (app.isPackaged) return [command, ...args];
  if (command === "inspect") return [path.join(root, "desktop/backend.py")];
  return ["-u", path.join(root, "polywav.py"), command, ...args];
}
let window;
let active = null;
let closing = false;

function send(event) {
  if (window && !window.isDestroyed()) window.webContents.send("job-event", event);
}

function runInspection(paths, container) {
  return new Promise((resolve, reject) => {
    const child = spawn(backend, backendArgs("inspect"), { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", chunk => stdout += chunk);
    child.stderr.on("data", chunk => stderr += chunk);
    child.on("error", reject);
    child.on("close", code => {
      try {
        const result = JSON.parse(stdout);
        if (code !== 0 || result.error) reject(new Error(result.error || stderr || "Inspection failed"));
        else resolve(result);
      } catch (error) { reject(new Error(stderr || error.message)); }
    });
    child.stdin.end(JSON.stringify({ paths, container }));
  });
}

async function startMerge(request) {
  if (active) throw new Error("A task is already running.");
  if (!request || !Array.isArray(request.paths) || !request.paths.length || request.paths.length > 128 ||
      typeof request.id !== "string" || typeof request.output !== "string" ||
      !["auto", "rf64"].includes(request.container) || request.alignment !== true || request.mapping !== true) {
    throw new Error("Invalid merge request.");
  }
  const task = { id: request.id, child: null, cancelRequested: false, pythonReady: false };
  active = task;
  let directory;
  try {
    directory = await fs.mkdtemp(path.join(os.tmpdir(), "poly-tools-"));
    const manifest = path.join(directory, "channels.json");
    await fs.writeFile(manifest, JSON.stringify({
      schema_version: 1, alignment: "sample_zero_confirmed",
      channels: request.paths.map((input, i) => ({ output_channel: i + 1, input })),
    }), { flag: "wx", mode: 0o600 });
    if (task.cancelRequested) {
      await fs.rm(directory, { recursive: true, force: true });
      active = null;
      send({ jobId: task.id, phase: "cancelled" });
      if (closing) window.close();
      return;
    }
    const child = spawn(backend, backendArgs("merge", ["--manifest", manifest,
      "--output", request.output, "--container", request.container]), { stdio: ["ignore", "pipe", "pipe"] });
    task.child = child;
    let stderr = "", pending = "";
    child.stderr.on("data", data => {
      if (!task.pythonReady) {
        task.pythonReady = true;
        if (task.cancelRequested) child.kill("SIGINT");
      }
      const text = data.toString();
      stderr += text;
      pending += text;
      const lines = pending.split(/[\r\n]/);
      pending = lines.pop();
      for (const line of lines) {
        const match = line.match(/(写入|校验):\s*(\d+)\/(\d+)/);
        if (match) send({ jobId: task.id, phase: match[1] === "写入" ? "merging" : "verifying", done: match[2], total: match[3] });
      }
    });
    let launchError = null;
    child.on("error", error => { launchError = error; });
    child.on("close", async (code) => {
      let cleanupWarning;
      try {
        await fs.rm(directory, { recursive: true, force: true });
      } catch (error) {
        cleanupWarning = `Failed to clean task manifest at ${directory}: ${error.message}`;
        stderr += `\n${cleanupWarning}`;
      }
      active = null;
      if (code === 0) send({ jobId: task.id, phase: "completed", output: request.output, channels: request.paths.length, warning: cleanupWarning });
      else if (code === 130) send({ jobId: task.id, phase: "cancelled", warning: cleanupWarning });
      else send({ jobId: task.id, phase: "failed", error: launchError?.message || stderr.trim() || `Backend exited (${code}).` });
      if (closing && window && !window.isDestroyed()) window.close();
    });
    send({ jobId: task.id, phase: "merging", done: "0", total: null });
  } catch (error) {
    if (directory) await fs.rm(directory, { recursive: true, force: true });
    active = null;
    throw error;
  }
}

ipcMain.handle("select-files", async () => {
  const result = await dialog.showOpenDialog(window, { properties: ["openFile", "multiSelections"], filters: [{ name: "WAV audio", extensions: ["wav"] }] });
  return result.canceled ? [] : result.filePaths;
});
ipcMain.handle("select-directory", async () => {
  const result = await dialog.showOpenDialog(window, { properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle("inspect", (_event, paths, container) => runInspection(paths, container));
ipcMain.handle("validate-output", async (_event, directory, name, paths) => {
  if (!directory || !name || path.basename(name) !== name || !name.toLowerCase().endsWith(".wav")) return "Choose a valid output file.";
  try {
    const stat = await fs.stat(directory);
    if (!stat.isDirectory()) return "Save location is not a folder.";
    await fs.access(directory, require("node:fs").constants.W_OK);
    const output = path.join(directory, name);
    const report = output.slice(0, -4) + ".json";
    for (const candidate of [output, report]) {
      try { await fs.lstat(candidate); return `Output already exists: ${candidate}`; }
      catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    const resolved = await fs.realpath(directory);
    const candidates = [path.join(resolved, name), path.join(resolved, name.slice(0, -4) + ".json")];
    for (const input of paths) {
      if (candidates.includes(await fs.realpath(input))) return "Output conflicts with a source file.";
    }
    return null;
  } catch (error) { return `Cannot use output folder: ${error.message}`; }
});
ipcMain.handle("start-merge", (_event, request) => startMerge(request));
ipcMain.handle("cancel", (_event, id) => {
  if (!active || active.id !== id) throw new Error("Task is no longer running.");
  active.cancelRequested = true;
  if (active.child && active.pythonReady && !active.child.kill("SIGINT")) throw new Error("Could not request cancellation.");
});
ipcMain.handle("show-in-folder", (_event, target) => shell.showItemInFolder(target));

app.whenReady().then(() => {
  window = new BrowserWindow({
    width: 1200, height: 800, minWidth: 960, minHeight: 640,
    backgroundColor: "#17191B", title: "POLY TOOLS",
    icon: path.join(root, "assets/icon.png"),
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  if (process.platform === "darwin" && existsSync(path.join(root, "assets/icon.png"))) {
    app.dock.setIcon(nativeImage.createFromPath(path.join(root, "assets/icon.png")));
  }
  window.webContents.on("will-navigate", event => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.on("close", event => {
    if (!active) return;
    event.preventDefault();
    if (closing) return;
    const choice = dialog.showMessageBoxSync(window, {
      type: "warning", buttons: ["Keep working", "Cancel task and close"], defaultId: 0, cancelId: 0,
      message: "A task is still running.", detail: "Cancel it and wait for cleanup before closing?",
    });
    if (choice === 1) {
      closing = true;
      active.cancelRequested = true;
      send({ jobId: active.id, phase: "cancelling" });
      if (active.child && active.pythonReady && !active.child.kill("SIGINT")) {
        closing = false;
        dialog.showErrorBox("Cancellation failed", "The task is still running. Please try again.");
      }
    }
  });
  window.loadFile(path.join(root, "dist/index.html"));
});
app.on("window-all-closed", () => app.quit());
