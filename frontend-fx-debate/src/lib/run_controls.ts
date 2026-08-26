export interface RunControlState {
  busy: boolean;
  cancelling: boolean;
}

/** Session history uses `active` while its latest attempt is still running. */
export function isSessionRunning(status: string | undefined): boolean {
  return status === "active" || status === "pending" || status === "running" || status === "waiting_user";
}

export function isRunActive(
  busy: boolean,
  status: string | undefined,
): boolean {
  return busy || status === "pending" || status === "running";
}

/**
 * Convert the cancellation API result into the local composer state.
 * The API can report no_active_loop when the server already completed the
 * attempt between the click and the cancellation request; that is still a
 * terminal state from the user's perspective.
 */
export function settleCancellation(
  state: RunControlState,
  status: string,
): RunControlState {
  if (status === "cancelled" || status === "no_active_loop") {
    return { busy: false, cancelling: false };
  }
  return { ...state, cancelling: false };
}
