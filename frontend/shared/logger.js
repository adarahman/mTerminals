// ============================================================
// shared/logger.js
// Replaces scattered console.log/warn/error calls (46 of them across the
// codebase as of this pass) with a single gated logger. In production
// you can flip Logger.level to 'warn' or 'silent' in one place instead
// of hunting down every console.* call.
//
// Load this before any file that references `Logger` — put it next to
// shared/config.js near the top of DashboardPro.html's <script> block.
//
// Usage:
//   Logger.info('DataService', 'WS connected');
//   Logger.warn('OptionChain', 'no rows for range', range);
//   Logger.error('PanelManager', `init failed for "${panel.name}"`, err);
//
// The first argument is treated as a module/component tag and rendered
// as a bracketed prefix, matching the existing `[panel-manager]`-style
// prefixes already used in panel-manager.js.
// ============================================================

const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3, silent: 4 };

const Logger = {
  // Defaults to verbose on localhost/dev, quieter everywhere else. Call
  // Logger.setLevel('debug'|'info'|'warn'|'error'|'silent') to override
  // at runtime (e.g. from the browser console while debugging).
  level: /^(localhost|127\.0\.0\.1)$/.test(location.hostname) ? 'debug' : 'warn',

  setLevel(level) {
    if (level in LOG_LEVELS) this.level = level;
  },

  _enabled(level) {
    return LOG_LEVELS[level] >= LOG_LEVELS[this.level];
  },

  _fmt(tag) {
    return tag ? `[${tag}]` : '';
  },

  debug(tag, ...args) {
    if (this._enabled('debug')) console.debug(this._fmt(tag), ...args);
  },
  info(tag, ...args) {
    if (this._enabled('info')) console.info(this._fmt(tag), ...args);
  },
  warn(tag, ...args) {
    if (this._enabled('warn')) console.warn(this._fmt(tag), ...args);
  },
  error(tag, ...args) {
    if (this._enabled('error')) console.error(this._fmt(tag), ...args);
  },
};

if (typeof window !== 'undefined') window.Logger = Logger;
if (typeof module !== 'undefined' && module.exports) module.exports = Logger;
