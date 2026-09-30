/**
 * LOCAL TIME — ONE formatter, shared by every page under /llm-tasks/.
 *
 * WHY THIS FILE EXISTS (the human, 2026-09-25):
 *     "display timezone will auto by my timezone as you have detect my computer
 *      to have my tz_offset_sec already"
 *     "have these function for all for http://127.0.0.1:18765/llm-tasks/"
 *
 * MEASURED, and this is the defect: the DB stores UTC and the UI rendered it
 * RAW. The same instant, three ways:
 *
 *     chat_identity_log.created_at        2026-09-25 06:00:01   (UTC, stored)
 *     SQLite CURRENT_TIMESTAMP            2026-09-25 06:25:03   (UTC)
 *     SQLite datetime('now','localtime')  2026-09-25 14:25:03   (the human's clock)
 *
 * The difference is 8 hours — exactly `tz_offset_sec / 3600`. MEASURED: there
 * were timestamp render sites and ZERO conversions, and `tz_offset_sec` was used
 * ONLY to build a "UTC+8" LABEL. The UI knew the offset and never applied it.
 *
 * WHY A MODULE AND NOT A FUNCTION IN app.js:
 * MEASURED: `app.js` had `fmtLocal`, but `evidence.js` had its OWN `fmtWhen`
 * (a second formatter that did NOT convert) and `mode-sessions.js` rendered raw
 * values. A formatter that lives inside one page cannot serve the others, so
 * each page grew its own — which is exactly how they drifted apart. ONE module,
 * imported by every page, is the only shape that cannot drift.
 *
 * THE OFFSET IS READ, NEVER HARD-CODED. MEASURED: `user_environment.tz_offset_sec`
 * is already 28800 (Asia/Hong_Kong), so a literal here would be a second source
 * of truth that disagrees the moment the human travels.
 *
 * AN UNKNOWN OFFSET DOES NOT SILENTLY SHOW UTC. It returns the raw value MARKED,
 * so a reader can tell "this is UTC because the offset is unknown" from "this is
 * my local time". A silent UTC display is the defect: the human cannot tell
 * which clock they are reading.
 */

/** The detected offset in seconds, or null when it is not known yet. */
let _tzOffsetSec = null;

/** The detected IANA zone name (e.g. "Asia/Hong_Kong"), or '' when unknown. */
let _tzName = '';

/**
 * Set the zone NAME from the detected environment. Purely a LABEL: the
 * conversion uses the offset, never this string. It exists so a column header
 * can say WHICH clock it shows, which is the user-friendly half of the
 * "2 timezones" defect (2026-09-25).
 */
export function setTzName(v) {
  _tzName = v == null ? '' : String(v).trim();
}

/** The current zone name, or ''. */
export function tzName() {
  return _tzName;
}

/**
 * A short label naming the clock the UI is showing, for a column header.
 *
 * MEASURED, and this is why it exists: the human saw TWO clocks on one screen
 * ("will have 2 timezone in my eye, it will make user confuse"). A header that
 * says `created_at (Asia/Hong_Kong)` removes the ambiguity at the point of
 * reading, instead of asking the reader to infer it from a badge elsewhere.
 *
 * An unknown offset is NAMED as unknown, never silently labelled UTC.
 */
export function tzLabel() {
  const off = _tzOffsetSec;
  if (off == null) return 'UTC (offset unknown)';
  const hours = off / 3600;
  const sign = hours >= 0 ? '+' : '';
  const shown = Number.isInteger(hours) ? String(hours) : hours.toFixed(1);
  const utc = hours === 0 ? 'UTC' : 'UTC' + sign + shown;
  return _tzName ? _tzName + ' ' + utc : utc;
}

/**
 * Set the offset from the detected environment. Called once the
 * `user_environment` row arrives. A non-numeric value clears it (null), so an
 * unknown offset is never mistaken for UTC.
 *
 * MEASURED DEFECT (caught by this proof, 2026-09-25): the first version was
 * `Number.isFinite(Number(v)) ? Number(v) : null`. `Number(null)` is **0**, and
 * 0 is finite — so an ABSENT offset became offset 0, i.e. UTC, and the UI
 * silently showed UTC while claiming to show local time. That is exactly the
 * defect this module exists to remove. `null` / `undefined` / `''` are now
 * rejected BEFORE the numeric coercion.
 */
export function setTzOffsetSec(v) {
  if (v == null || v === '') {
    _tzOffsetSec = null;
    return;
  }
  const n = Number(v);
  _tzOffsetSec = Number.isFinite(n) ? n : null;
}

/** The current offset in seconds, or null. */
export function tzOffsetSec() {
  return _tzOffsetSec;
}

/**
 * Render a stored UTC timestamp in the human's clock.
 *
 * @param {*} ts  a stored UTC string, e.g. "2026-09-25 06:00:01"
 * @returns {string} the local time, or the raw value MARKED when the offset is
 *                   unknown, or '-' when the input is empty.
 */
export function fmtLocal(ts) {
  const raw = String(ts == null ? '' : ts).trim();
  if (!raw) return '-';
  const off = _tzOffsetSec;
  if (off == null) {
    // UNKNOWN offset: show the raw value AND say so. Never a silent UTC.
    return raw + ' UTC?';
  }
  // The stored value is UTC. Parse it as UTC, then shift by the offset.
  // `Date.parse` on "YYYY-MM-DD HH:MM:SS" is implementation-defined, so the
  // parts are read explicitly and the string is normalised to ISO first.
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
  if (!m) return raw;
  const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  const d = new Date(ms + off * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) +
    ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
}
