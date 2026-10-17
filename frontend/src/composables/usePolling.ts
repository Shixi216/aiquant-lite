import { onBeforeUnmount, onMounted } from "vue";

/** Returns true during A-share trading sessions in Beijing wall-clock time. */
export function isTradingTime(now = new Date()): boolean {
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = now.getHours() * 60 + now.getMinutes();
  return minutes >= 9 * 60 + 10 && minutes <= 15 * 60 + 5;
}

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
    timer = setTimeout(tick, interval);
  }

  onMounted(() => {
    timer = setTimeout(tick, activeMs);
  });

  onBeforeUnmount(() => {
    stopped = true;
    if (timer) clearTimeout(timer);
  });
}
