import { onBeforeUnmount, onMounted } from "vue";
import { isTradingTime } from "../domain/marketTime";
export { isTradingTime } from "../domain/marketTime";

/**
 * Poll `task` every `activeMs` during trading hours, `idleMs` otherwise.
 * The timer is rescheduled after each run; errors are swallowed so a
 * transient router outage never kills the loop.
 */
export function usePolling(task: () => Promise<void>, activeMs: number, idleMs: number) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  async function tick() {
    if (stopped) return;
    try {
      await task();
    } catch {
      // keep polling through transient failures
    }
    const interval = isTradingTime() ? activeMs : idleMs;
    if (!stopped) timer = setTimeout(tick, interval);
  }

  onMounted(() => {
    timer = setTimeout(tick, isTradingTime() ? activeMs : idleMs);
  });

  onBeforeUnmount(() => {
    stopped = true;
    if (timer) clearTimeout(timer);
  });
}
