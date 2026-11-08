import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { adapter, isDemo } from "./adapter";
import { initialTask, taskReducer } from "./state.mjs";
import type { Inspection, JobEvent, Source } from "./types";
import "./style.css";

const fileName = (path: string) => path.split(/[\\/]/).pop() || path;
const natural = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const displayError = (error: unknown) => error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, "") : String(error);
const duration = (source: Source) => {
  const ms = BigInt(source.frames) * 1000n / BigInt(source.rate);
  const hh = ms / 3600000n, mm = (ms / 60000n) % 60n, ss = (ms / 1000n) % 60n;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(ms % 1000n).padStart(3, "0")}`;
};
const sizeText = (size: string | null) => {
  if (size === null) return "—";
  const n = Number(size);
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)} KB`;
  return `${size} bytes`;
};
const outputPath = (directory: string, name: string) => directory.replace(/[\\/]+$/, "") + (directory.includes("\\") && !directory.includes("/") ? "\\" : "/") + name;

function Logo() {
  return <svg className="logo" width="43" height="43" viewBox="0 0 43 43" aria-hidden="true">
    <path d="M4 8h29a5 5 0 0 1 5 5v20a4 4 0 0 1-4 4H23M9 16h29M14 24h24M23 32h15" fill="none" stroke="currentColor" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}
function AppHeader() {
  return <header className="app-header"><Logo /><div><h1>POLY TOOLS</h1><p>Mono to multichannel WAV</p></div>{isDemo && <span className="demo">Demo · No audio is processed</span>}</header>;
}
function App() {
  const [paths, setPaths] = useState<string[]>([]);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [directory, setDirectory] = useState("");
  const [name, setName] = useState("Session_poly.wav");
  const [container, setContainer] = useState("auto");
  const [mapping, setMapping] = useState(false);
  const [alignment, setAlignment] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const [drag, setDrag] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [outputError, setOutputError] = useState<string | null>(null);
  const [checkingOutput, setCheckingOutput] = useState(false);
  const [task, dispatch] = useReducer(taskReducer, initialTask);
  const sequence = useRef(0);
  const currentJob = useRef<string | null>(null);
  const busy = ["merging", "verifying", "cancelling"].includes(task.phase);
  const locked = busy || task.phase === "inspecting";
  const sourceMap = useMemo(() => new Map(inspection?.files.map(item => [item.path, item]) ?? []), [inspection]);
  const nameError = !name.trim() ? "Enter a file name." : /[<>:"/\\|?*\x00-\x1f]/.test(name) || name === "." || name === ".." ? "File name contains invalid characters." : !name.toLowerCase().endsWith(".wav") ? "File name must end in .wav." : "";
  const ready = !!inspection && paths.length > 0 && inspection.files.length === paths.length &&
    inspection.errors.length === 0 && mapping && alignment && !!directory && !nameError && !outputError && !checkingOutput && !isDemo;

  const inspect = useCallback(async (nextPaths: string[], nextContainer: string) => {
    const id = ++sequence.current;
    setInspection(null);
    if (!nextPaths.length) { dispatch({ type: "status", phase: "empty" }); return; }
    dispatch({ type: "status", phase: "inspecting" });
    try {
      const result = await adapter.inspect(nextPaths, nextContainer);
      if (id !== sequence.current) return;
      setInspection(result);
      dispatch({ type: "status", phase: "needs_review" });
    } catch (error) {
      if (id === sequence.current) dispatch({ type: "error", error: displayError(error) });
    }
  }, []);

  useEffect(() => adapter.subscribe((event: JobEvent) => {
    if (event.jobId === currentJob.current) {
      if (event.warning) setMessage(event.warning);
      dispatch({ type: "event", event });
    }
  }), []);
  useEffect(() => {
    if (!details) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setDetails(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [details]);
  useEffect(() => {
    let active = true;
    if (!directory || nameError || isDemo) { setOutputError(null); setCheckingOutput(false); return; }
    setCheckingOutput(true);
    adapter.validateOutput(directory, name, paths).then(error => {
      if (active) { setOutputError(error); setCheckingOutput(false); }
    }).catch(error => {
      if (active) { setOutputError(displayError(error)); setCheckingOutput(false); }
    });
    return () => { active = false; };
  }, [directory, name, nameError, paths]);
  useEffect(() => {
    if (!["empty", "inspecting", "merging", "verifying", "cancelling", "completed", "failed", "cancelled"].includes(task.phase)) {
      dispatch({ type: "status", phase: ready ? "ready" : "needs_review" });
    }
  }, [ready, task.phase]);

  const updatePaths = (next: string[], resetAlignment = true) => {
    setPaths(next); setMapping(false); if (resetAlignment) setAlignment(false);
    setSelected(current => current && next.includes(current) ? current : null);
    setMessage(""); inspect(next, container);
  };
  const addPaths = (incoming: string[]) => {
    if (!incoming.length) return;
    if (incoming.some(p => !p)) { setMessage("Some files could not be accessed. Use Add files in the desktop app."); return; }
    if (paths.length + incoming.length > 128) { setMessage("A task supports at most 128 files. No files were added."); return; }
    if (new Set([...paths, ...incoming]).size !== paths.length + incoming.length) {
      setMessage("A selected file is already in the list. No files were added."); return;
    }
    updatePaths([...paths, ...incoming.sort((a, b) => natural.compare(fileName(a), fileName(b)))]);
  };
  const chooseFiles = async () => {
    try { addPaths(await adapter.selectFiles()); } catch (error) { setMessage(displayError(error)); }
  };
  const move = (from: number, to: number) => {
    if (from < 0 || to < 0 || from >= paths.length || to >= paths.length || from === to) return;
    const next = [...paths]; const [item] = next.splice(from, 1); next.splice(to, 0, item);
    updatePaths(next, false);
  };
  const merge = async () => {
    if (!ready || busy) return;
    const id = crypto.randomUUID(); currentJob.current = id;
    dispatch({ type: "start", jobId: id }); setMessage("");
    try {
      await adapter.startMerge({ id, paths, output: outputPath(directory, name), container, alignment, mapping });
    } catch (error) { dispatch({ type: "error", error: displayError(error) }); }
  };
  const cancel = async () => {
    if (!currentJob.current) return;
    const previousPhase = task.phase === "verifying" ? "verifying" : "merging";
    dispatch({ type: "cancel" });
    try { await adapter.cancel(currentJob.current); }
    catch (error) { dispatch({ type: "cancel_failed", phase: previousPhase }); setMessage(`Cancellation request failed: ${displayError(error)}`); }
  };
  const retry = () => { setMapping(false); setAlignment(false); setMessage(""); inspect(paths, container); };
  const currentStatus = task.phase === "inspecting" ? "Reading files…" : inspection?.errors.length ? "Review required" :
    ready ? "Ready to merge" : paths.length ? "Review required" : "Add files to get started";
  const errors = [...(inspection?.errors ?? []), ...(outputError ? [outputError] : []), ...(message ? [message] : [])];
  const listLabel = paths.length === 1 ? "1 file" : `${paths.length} files`;
  const currentSource = paths.length ? sourceMap.get(paths[0]) : undefined;
  const summary = currentSource && !inspection?.errors.length
    ? `${currentSource.rate / 1000} kHz  ·  ${currentSource.bits}-bit ${currentSource.encoding === "pcm" ? "PCM" : "float"}  ·  Same length`
    : paths.length ? "Review source files and format" : "Original audio remains unchanged";
  const pct = task.done !== null && task.total !== null ? (BigInt(task.total) === 0n ? 100 : Number(BigInt(task.done) * 1000n / BigInt(task.total)) / 10) : null;
  return <div className="app" onDragOver={e => { if (!locked && e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
    onDrop={e => { if (!locked && e.dataTransfer.files.length) { e.preventDefault(); addPaths(Array.from(e.dataTransfer.files).map(file => adapter.pathForFile(file))); } }}>
    <AppHeader />
    <main className="workspace">
      <section className="sources" aria-labelledby="sources-heading">
        <div className="section-heading"><div className="heading-with-count"><h2 id="sources-heading">Source files</h2><span>{listLabel}</span></div>
          <button className="button secondary add" disabled={locked} onClick={chooseFiles}><span aria-hidden="true">＋</span> {task.phase === "inspecting" ? "Reading files…" : "Add files"}</button></div>
        {paths.length ? <>
          <div className="list-tools">
            <span>Drag to reorder · Select a row to edit</span>
            <div className="tool-buttons">
              <button disabled={locked || !selected || paths.indexOf(selected) === 0} onClick={() => move(paths.indexOf(selected!), paths.indexOf(selected!) - 1)} aria-label="Move selected file up" title="Move up">↑</button>
              <button disabled={locked || !selected || paths.indexOf(selected) === paths.length - 1} onClick={() => move(paths.indexOf(selected!), paths.indexOf(selected!) + 1)} aria-label="Move selected file down" title="Move down">↓</button>
              <button disabled={locked || !selected} onClick={() => updatePaths(paths.filter(p => p !== selected))}>Remove</button>
              <button disabled={locked} onClick={() => updatePaths([])}>Clear</button>
            </div>
          </div>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Source files; select a row to reorder or remove"
            onKeyDown={e => { if (!locked && selected && (e.key === "Delete" || e.key === "Backspace")) { e.preventDefault(); updatePaths(paths.filter(p => p !== selected)); } }}>
            <table><thead><tr><th aria-label="Reorder"></th><th>Channel</th><th>File</th><th>Length</th></tr></thead>
              <tbody>{paths.map((path, i) => { const source = sourceMap.get(path); return <tr key={path} className={selected === path ? "selected" : ""}
                draggable={!locked} onDragStart={e => { setDrag(path); e.dataTransfer.effectAllowed = "move"; }}
                onDragOver={e => { if (drag && !locked) e.preventDefault(); }}
                onDrop={e => { e.preventDefault(); if (drag) move(paths.indexOf(drag), i); setDrag(null); }}
                onDragEnd={() => setDrag(null)} onClick={() => setSelected(path)}>
                <td className="handle" aria-label="Drag to reorder">⠿</td><td className="channel"><button className="row-select" aria-label={`Select channel ${i + 1}: ${fileName(path)}`} aria-pressed={selected === path} onClick={() => setSelected(path)}>{String(i + 1).padStart(3, "0")}</button></td>
                <td className="filename" title={path}><span>{fileName(path)}</span></td><td className="length">{source ? duration(source) : "—"}</td></tr>; })}</tbody></table>
          </div>
        </> : <div className="empty">
          <div className="empty-icon" aria-hidden="true"><span>↓</span></div><strong>Drop mono WAV files here</strong><p>Up to 128 channels</p>
          <button className="button secondary" onClick={chooseFiles} disabled={locked}>Add files</button></div>}
        <div className="summary">{summary}{inspection?.errors.length ? <button className="text-button" onClick={() => setDetails(true)}>Review details</button> : null}</div>
      </section>
      <section className="export" aria-labelledby="export-heading">
        <h2 id="export-heading">Export</h2>
        <label htmlFor="filename">File name</label>
        <input id="filename" value={name} onChange={e => { setName(e.target.value); if (task.phase === "completed") dispatch({ type: "status", phase: "needs_review" }); }} disabled={locked} aria-invalid={!!nameError} aria-describedby={nameError ? "filename-error" : undefined} />
        {nameError && <p className="field-error" id="filename-error">{nameError}</p>}
        <label htmlFor="directory">Save to</label>
        <div className="directory-field"><input id="directory" readOnly value={directory} placeholder="Choose folder…" title={directory} />
          <button disabled={locked} aria-label="Choose output folder" onClick={async () => { try { const result = await adapter.selectOutputDirectory(); if (result) { setDirectory(result); if (task.phase === "completed") dispatch({ type: "status", phase: "needs_review" }); } } catch (error) { setMessage(displayError(error)); } }}><span aria-hidden="true">▱</span></button></div>
        <label htmlFor="format">Format</label>
        <select id="format" value={container} disabled={locked} onChange={e => { setContainer(e.target.value); inspect(paths, e.target.value); }}>
          <option value="auto">Auto (WAV / RF64)</option><option value="rf64">RF64</option></select>
        <div className="estimate"><strong>{paths.length} {paths.length === 1 ? "channel" : "channels"}</strong><p>Estimated size {sizeText(inspection?.size ?? null)}</p></div>
        <div className={`readiness ${ready ? "good" : "review"}`}><span aria-hidden="true">{ready ? "✓" : "○"}</span><strong>{currentStatus}</strong></div>
        {paths.length > 0 && <div className="confirmations">
          <label><input type="checkbox" checked={mapping} disabled={locked || !!inspection?.errors.length || !inspection} onChange={e => setMapping(e.target.checked)} /> <span>I checked the channel order and take</span></label>
          <label><input type="checkbox" checked={alignment} disabled={locked || !!inspection?.errors.length || !inspection} onChange={e => setAlignment(e.target.checked)} /> <span>Align files at sample 0</span></label>
          <p>{inspection?.files.some(file => file.timeReference === null) ? "Missing time reference means the start is unverified." : "Matching time references do not prove the same take."}</p>
        </div>}
        {(paths.length > 0 || errors.length > 0) && <button className="text-button details-link" onClick={() => setDetails(!details)} aria-expanded={details}>Review details {details ? "↑" : "↗"}</button>}
        {(inspection?.warnings.length || errors.length) ? <p className="inline-notice" role="status">{errors[0] || inspection?.warnings[0]}</p> : null}
      </section>
    </main>
    <footer className="footer">
      <div className="footer-status" aria-live="polite">
        {busy ? <><div className="progress-label"><strong>{task.phase === "cancelling" ? "Cancelling…" : task.phase === "verifying" ? "Verifying…" : "Merging…"}</strong>{pct !== null && <span>{pct.toFixed(1)}%</span>}</div>
          <progress aria-label={task.phase === "verifying" ? "Verifying" : "Merging"} max="100" {...(pct === null ? {} : { value: pct })} /></> :
          task.phase === "completed" ? <span className="success">✓ &nbsp;Verified · {task.channels} / {task.channels} channels match</span> :
          task.phase === "failed" ? <span className="failure">Merge failed · Review details and try again</span> :
          task.phase === "cancelled" ? <span>Cancelled</span> : <span>Original audio preserved</span>}
      </div>
      {busy ? <button className="button secondary footer-button" onClick={cancel} disabled={task.phase === "cancelling"}>{task.phase === "cancelling" ? "Cancelling…" : "Cancel"}</button> :
        task.phase === "completed" ? <button className="button primary footer-button" onClick={() => adapter.showInFolder(task.output).catch(error => setMessage(displayError(error)))}>Show in folder</button> :
        task.phase === "failed" ? <button className="button primary footer-button" onClick={retry}>Try again</button> :
        <button className="button primary footer-button" onClick={merge} disabled={!ready || locked}>Merge files</button>}
    </footer>
    {details && <div className="drawer-backdrop" onClick={() => setDetails(false)}><aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="details-title" onClick={e => e.stopPropagation()}>
      <div className="drawer-heading"><h2 id="details-title">Review details</h2><button className="icon-button" aria-label="Close details" onClick={() => setDetails(false)}>×</button></div>
      <p className="drawer-intro">Check the take, source order and timing before merging. Output channel numbers follow the list.</p>
      {task.error && <div className="problem"><strong>Task error</strong><pre>{task.error}</pre></div>}
      {errors.map((error, i) => <div className="problem" key={i}><strong>Issue</strong><pre>{error}</pre></div>)}
      {inspection?.warnings.map(warning => <div className="warning" key={warning}>{warning}</div>)}
      <div className="details-list">{paths.map((path, i) => { const source = sourceMap.get(path); return <div className="detail-row" key={path}>
        <strong>{String(i + 1).padStart(3, "0")} · {fileName(path)}</strong><span title={path}>{path}</span>
        {source && <span>{source.rate} Hz · {source.bits}-bit {source.encoding} · {source.validBits} valid bits<br />{source.frames} frames · BWF reference: {source.timeReference ?? "missing"} · Candidate channel: {source.candidate ?? "unknown"}{source.segment !== null ? ` · Segment: ${source.segment}` : ""}</span>}</div>; })}</div>
    </aside></div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<App />);
