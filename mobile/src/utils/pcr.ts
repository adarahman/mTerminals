type OiRow = {
  ceOI?: unknown;
  peOI?: unknown;
  ceChgOI?: unknown;
  peChgOI?: unknown;
};

// PCR now minus PCR at the previous close, using the supplied strike range.
export function formatPcrChange(chain: unknown): string {
  if (!Array.isArray(chain) || !chain.length) return '—';
  let ce = 0, pe = 0, ceChange = 0, peChange = 0;
  for (const row of chain as OiRow[]) {
    if (!row) return '—';
    const raw = [row.ceOI, row.peOI, row.ceChgOI, row.peChgOI];
    if (raw.some(value => value == null || value === '')) return '—';
    const values = raw.map(Number);
    if (!values.every(Number.isFinite)) return '—';
    ce += values[0];
    pe += values[1];
    ceChange += values[2];
    peChange += values[3];
  }
  const previousCe = ce - ceChange;
  const previousPe = pe - peChange;
  if (ce <= 0 || previousCe <= 0 || pe < 0 || previousPe < 0) return '—';
  const change = pe / ce - previousPe / previousCe;
  if (!Number.isFinite(change)) return '—';
  const rounded = Number(change.toFixed(3));
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(3)}`;
}
