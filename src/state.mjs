export const initialTask = { phase: "empty", jobId: null, done: null, total: null, error: "", output: "", channels: 0 };
export function taskReducer(state, action) {
  if (action.type === "status") {
    if (["merging", "verifying", "cancelling"].includes(state.phase)) return state;
    return { ...state, phase: action.phase, error: action.error || "" };
  }
  if (action.type === "start") return { ...initialTask, phase: "merging", jobId: action.jobId };
  if (action.type === "error") return { ...state, phase: "failed", error: action.error };
  if (action.type === "cancel") return { ...state, phase: "cancelling" };
  if (action.type === "cancel_failed" && state.phase === "cancelling") return { ...state, phase: action.phase };
  if (action.type === "event") {
    const event = action.event;
    if (event.jobId !== state.jobId) return state;
    if (state.phase === "cancelling" && ["merging", "verifying"].includes(event.phase)) return state;
    return { ...state, phase: event.phase, done: event.done ?? null, total: event.total ?? null,
      error: event.error || "", output: event.output || state.output, channels: event.channels || state.channels };
  }
  return state;
}
