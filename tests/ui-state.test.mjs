import { test } from "node:test";
import assert from "node:assert/strict";
import { initialTask, taskReducer } from "../src/state.mjs";

test("events from old jobs are ignored and verification precedes success", () => {
  let state = taskReducer(initialTask, { type: "start", jobId: "new" });
  assert.equal(taskReducer(state, { type: "event", event: { jobId: "old", phase: "completed" } }), state);
  state = taskReducer(state, { type: "event", event: { jobId: "new", phase: "verifying", done: "1", total: "2" } });
  assert.equal(state.phase, "verifying");
  state = taskReducer(state, { type: "event", event: { jobId: "new", phase: "completed", channels: 2 } });
  assert.equal(state.phase, "completed");
  assert.equal(state.channels, 2);
});

test("cancellation waits for a terminal event and ignores late progress", () => {
  let state = taskReducer(initialTask, { type: "start", jobId: "id" });
  state = taskReducer(state, { type: "cancel" });
  state = taskReducer(state, { type: "event", event: { jobId: "id", phase: "merging" } });
  assert.equal(state.phase, "cancelling");
  state = taskReducer(state, { type: "event", event: { jobId: "id", phase: "cancelled" } });
  assert.equal(state.phase, "cancelled");
});

test("failure is never treated as successful verification", () => {
  let state = taskReducer(initialTask, { type: "start", jobId: "id" });
  state = taskReducer(state, { type: "event", event: { jobId: "id", phase: "failed", error: "checksum mismatch" } });
  assert.equal(state.phase, "failed");
  assert.equal(state.error, "checksum mismatch");
});
