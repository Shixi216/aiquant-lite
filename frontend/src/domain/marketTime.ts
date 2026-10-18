/** A-share monitoring window in Shanghai time; exchange holidays need a calendar. */
const marketClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Shanghai", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function isTradingTime(now = new Date()): boolean {
  if (!Number.isFinite(now.getTime())) return false;
  const parts = Object.fromEntries(marketClock.formatToParts(now).map((part) => [part.type, part.value]));
  if (parts.weekday === "Sat" || parts.weekday === "Sun") return false;
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return minutes >= 9 * 60 + 10 && minutes <= 15 * 60 + 5;
}
