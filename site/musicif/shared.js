// Shared schedule helpers (used by player and admin)
const SM = (() => {
  const DAYS = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const hm = s => { const [h, m] = String(s).split(':').map(Number); return h * 60 + (m || 0); };
  const fmt = min => String(Math.floor(min / 60) % 24).padStart(2, '0') + ':' + String(min % 60).padStart(2, '0');

  /** Current day-of-week (1=Mon), minute-of-day and date in the given IANA time zone. */
  function zonedNow(tz, date = new Date()) {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).formatToParts(date);
    const g = t => (parts.find(p => p.type === t) || {}).value;
    return {
      dow: { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 }[g('weekday')],
      min: +g('hour') * 60 + +g('minute'),
      sec: +g('second'),
      date: `${g('year')}-${g('month')}-${g('day')}`,
      hhmm: `${g('hour')}:${g('minute')}`,
    };
  }

  const applies = (s, dow, min) => s.enabled !== false && (s.days || []).includes(dow) && hm(s.start) <= min && min < hm(s.end);

  /** First matching slot wins (list order = priority). */
  const activeSlot = (slots, dow, min) => (slots || []).find(s => applies(s, dow, min)) || null;

  /** Next time the schedule changes: {slot (null = silence), dow, min, inDays} */
  function nextChange(slots, dow, min) {
    const cur = activeSlot(slots, dow, min);
    for (let i = 1; i <= 7 * 1440; i++) {
      const t = min + i, d = ((dow - 1 + Math.floor(t / 1440)) % 7) + 1, m = t % 1440;
      const s = activeSlot(slots, d, m);
      if ((s && s.id) !== (cur && cur.id)) return { slot: s, dow: d, min: m, inDays: Math.floor(t / 1440) };
    }
    return null;
  }

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const mmss = ms => { const s = Math.max(0, Math.round(ms / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  return { DAYS, hm, fmt, zonedNow, activeSlot, nextChange, esc, mmss, sleep };
})();
