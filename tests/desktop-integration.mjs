import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { copyFile, mkdtemp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const root = path.resolve(".");
const temp = await mkdtemp(path.join(os.tmpdir(), "poly-ui-test-"));
const port = 19227;
const packaged = process.argv.includes("--packaged");
const executable = packaged
  ? (process.env.POLY_TEST_APP || path.join(root, "release/mac-arm64/POLY TOOLS.app/Contents/MacOS/POLY TOOLS"))
  : path.join(root, "node_modules/.bin/electron");
const app = spawn(executable, [...(packaged ? [] : ["."]), `--remote-debugging-port=${port}`], { stdio: "ignore",
  cwd: packaged ? temp : root,
  env: packaged ? { ...process.env, PATH: "/usr/bin:/bin", POLY_TOOLS_PYTHON: "/nonexistent/python" } : process.env });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws;
try {
  let pages;
  for (let i = 0; i < 100; i++) {
    try { pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (pages.length) break; }
    catch {}
    await wait(100);
  }
  assert.ok(pages?.length, "desktop app must open a window");
  ws = new WebSocket(pages[0].webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  ws.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (!pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id); pending.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : resolve(message.result);
  };
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression, awaitPromise = true) => {
    const result = await cdp("Runtime.evaluate", { expression, awaitPromise, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await cdp("Runtime.enable");
  await cdp("DOM.enable");
  await wait(300);
  assert.equal(await evaluate("document.title"), "POLY TOOLS");
  assert.equal(await evaluate("!!window.polyTools"), true);
  const iconSvg = await readFile(path.join(root, "assets/icon.svg"), "utf8");
  const icon = await evaluate(`new Promise((resolve,reject) => {const image=new Image();image.onload=()=>{const canvas=document.createElement('canvas');canvas.width=512;canvas.height=512;canvas.getContext('2d').drawImage(image,0,0);resolve(canvas.toDataURL('image/png').split(',')[1]);};image.onerror=reject;image.src=${JSON.stringify("data:image/svg+xml;base64," + Buffer.from(iconSvg).toString("base64"))};})`);
  await writeFile(path.join(root, "assets/icon.png"), Buffer.from(icon, "base64"));
  const screenshotDir = path.join(root, "screenshots");
  await mkdir(screenshotDir, { recursive: true });
  async function screenshot(name, width, height) {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    await wait(100);
    const data = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(path.join(screenshotDir, name), Buffer.from(data.data, "base64"));
  }
  await screenshot("desktop-empty.png", 1200, 800);
  const fixture = path.join(temp, "CH1.wav");
  execFileSync("python3", ["-c", "import wave,sys; f=wave.open(sys.argv[1],'wb'); f.setparams((1,2,48000,0,'NONE','not compressed')); f.writeframes(bytes.fromhex('0080ff7f')*50); f.close()", fixture]);
  const inspection = await evaluate(`window.polyTools.inspect(${JSON.stringify([fixture])}, 'auto')`);
  assert.equal(inspection.files.length, 1);
  assert.deepEqual(inspection.errors, []);
  assert.equal(inspection.files[0].frames, "100");
  assert.equal(await evaluate(`window.polyTools.validateOutput(${JSON.stringify(temp)}, "output.wav", ${JSON.stringify([fixture])})`), null);
  assert.ok(await evaluate(`window.polyTools.validateOutput(${JSON.stringify(temp)}, "CH1.wav", ${JSON.stringify([fixture])})`));
  // Use Chromium's file chooser input to deliver genuine local File handles to the drop target.
  await evaluate("document.body.insertAdjacentHTML('beforeend', '<input id=\"test-file\" type=\"file\" multiple>')");
  const doc = await cdp("DOM.getDocument");
  const node = await cdp("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#test-file" });
  await cdp("DOM.setFileInputFiles", { nodeId: node.nodeId, files: [fixture] });
  await evaluate(`(() => { const transfer = new DataTransfer(); transfer.items.add(document.querySelector('#test-file').files[0]); document.querySelector('.app').dispatchEvent(new DragEvent('drop', { bubbles:true, cancelable:true, dataTransfer: transfer })); })()`);
  for (let i = 0; i < 100 && !await evaluate("document.body.innerText.includes('CH1.wav')"); i++) await wait(50);
  assert.equal(await evaluate("document.body.innerText.includes('CH1.wav')"), true);
  for (let i = 0; i < 100 && !await evaluate("document.body.innerText.includes('00:00:00.002')"); i++) await wait(50);
  assert.equal(await evaluate("document.body.innerText.includes('00:00:00.002')"), true,
    await evaluate("document.body.innerText"));
  await screenshot("desktop-review.png", 1200, 800);
  await screenshot("desktop-minimum.png", 960, 640);
  const bottomVisible = await evaluate("(() => { const r = document.querySelector('.footer-button').getBoundingClientRect(); return r.bottom <= innerHeight && r.top >= 0; })()");
  assert.equal(bottomVisible, true, "primary action must remain visible at minimum size");
  const output = path.join(temp, "output.wav");
  const mergeResult = await evaluate(`new Promise(async resolve => {
    const events = []; const unsub = window.polyTools.subscribe(e => { if (e.jobId === 'integration') {
      events.push(e.phase); if (['completed','failed','cancelled'].includes(e.phase)) { unsub(); resolve({event:e, events}); }
    }});
    try { await window.polyTools.startMerge({id:'integration', paths:${JSON.stringify([fixture])}, output:${JSON.stringify(output)}, container:'auto', alignment:true, mapping:true}); }
    catch(e) { unsub(); resolve({error:String(e)}); }
  })`);
  assert.equal(mergeResult.event?.phase, "completed", JSON.stringify(mergeResult));
  assert.ok(mergeResult.events.includes("verifying"), "verification must be reported before completion");
  assert.ok((await readFile(output)).length > 68);
  const report = JSON.parse(await readFile(path.join(temp, "output.json"), "utf8"));
  assert.equal(report.validation.status, "passed");
  assert.equal(report.channels[0].input, "CH1.wav");
  const failure = await evaluate(`new Promise(async resolve => {
    const unsub = window.polyTools.subscribe(e => { if(e.jobId==='collision' && ['completed','failed','cancelled'].includes(e.phase)) { unsub(); resolve(e); }});
    try { await window.polyTools.startMerge({id:'collision',paths:${JSON.stringify([fixture])},output:${JSON.stringify(output)},container:'auto',alignment:true,mapping:true}); }
    catch(e) { unsub(); resolve({phase:'failed',error:String(e)}); }
  })`);
  assert.equal(failure.phase, "failed");
  const bigFixture = path.join(temp, "large.wav");
  execFileSync("python3", ["-c", "import wave,sys; f=wave.open(sys.argv[1],'wb'); f.setparams((1,2,48000,0,'NONE','not compressed')); b=bytes(range(256))*4096; [f.writeframesraw(b) for _ in range(64)]; f.close()", bigFixture]);
  const before = await stat(bigFixture);
  const cancelledOutput = path.join(temp, "cancelled.wav");
  const cancelled = await evaluate(`new Promise(async resolve => {
    const timer=setTimeout(()=>resolve({phase:'timeout'}),30000);
    const unsub=window.polyTools.subscribe(e=>{if(e.jobId==='cancel-test' && ['completed','cancelled','failed'].includes(e.phase)) {clearTimeout(timer);unsub();resolve(e);}});
    try { await window.polyTools.startMerge({id:'cancel-test',paths:${JSON.stringify([bigFixture])},output:${JSON.stringify(cancelledOutput)},container:'auto',alignment:true,mapping:true}); await window.polyTools.cancel('cancel-test'); }
    catch(e) {clearTimeout(timer);unsub();resolve({phase:'failed',error:String(e)});}
  })`);
  assert.equal(cancelled.phase, "cancelled", JSON.stringify(cancelled));
  assert.equal((await stat(bigFixture)).size, before.size);
  await assert.rejects(stat(cancelledOutput), { code: "ENOENT" });
  await evaluate(`(() => { const e=document.querySelector('#filename'); const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; setter.call(e,'bad/name'); e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await screenshot("desktop-error.png", 1200, 800);
  assert.ok(await evaluate("document.body.innerText.includes('File name contains invalid characters.')"));
  await evaluate(`(() => { const e=document.querySelector('#filename'); const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; setter.call(e,'Session_poly.wav'); e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  const many = [fixture];
  for (let i = 2; i <= 128; i++) {
    const target = path.join(temp, `CH${i}.wav`);
    await copyFile(fixture, target);
    many.push(target);
  }
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Clear').click()");
  await cdp("DOM.setFileInputFiles", { nodeId: node.nodeId, files: many.slice(0, 12) });
  await evaluate(`(() => { const transfer=new DataTransfer(); for(const f of document.querySelector('#test-file').files) transfer.items.add(f); document.querySelector('.app').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer})); })()`);
  for (let i = 0; i < 100 && !await evaluate("document.body.innerText.includes('12 channels')"); i++) await wait(50);
  assert.equal(await evaluate("document.body.innerText.includes('12 channels')"), true);
  await screenshot("desktop-12-channels.png", 1200, 800);
  for (let i = 0; i < 100 && await evaluate("document.body.innerText.includes('Reading files')"); i++) await wait(50);
  await evaluate("document.querySelectorAll('.row-select')[1].click()");
  await wait(50);
  await evaluate("document.querySelector('[aria-label=\"Move selected file up\"]').click()");
  for (let i = 0; i < 100 && await evaluate("document.body.innerText.includes('Reading files')"); i++) await wait(50);
  assert.equal(await evaluate("document.querySelector('tbody tr .filename').textContent"), "CH2.wav");
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Clear').click()");
  await cdp("DOM.setFileInputFiles", { nodeId: node.nodeId, files: many });
  await evaluate(`(() => { const transfer=new DataTransfer(); for(const f of document.querySelector('#test-file').files) transfer.items.add(f); document.querySelector('.app').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer})); })()`);
  for (let i = 0; i < 100 && !await evaluate("document.body.innerText.includes('128 channels')"); i++) await wait(50);
  assert.equal(await evaluate("document.body.innerText.includes('128 channels')"), true);
  assert.equal(await evaluate("document.querySelectorAll('tbody tr').length"), 128);
  for (let i = 0; i < 100 && await evaluate("document.body.innerText.includes('Reading files')"); i++) await wait(50);
  assert.equal(await evaluate("document.body.innerText.includes('128 files')"), true);
  console.log("Desktop integration passed: file drop, 1/12/128 channels, real merge, verification, cancellation, collision and layouts.");
} finally {
  ws?.close();
  app.kill("SIGTERM");
  await rm(temp, { recursive: true, force: true });
}
