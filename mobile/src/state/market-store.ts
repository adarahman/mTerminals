export type MarketPayload = Record<string, any>;

export type ConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'error'
  | 'disconnected';

type Listener = () => void;

type MarketSnapshot = {
  payload: MarketPayload | null;
  connected: boolean;
  connectionStatus: ConnectionStatus;
  error: string | null;
};

const listeners = new Set<Listener>();

let snapshot: MarketSnapshot = {
  payload: null,
  connected: false,
  connectionStatus: 'disconnected',
  error: null,
};

function emit() {
  for (const listener of listeners) {
    listener();
  }
}

function update(next: Partial<MarketSnapshot>) {
  const changed = Object.entries(next).some(
    ([key, value]) =>
      snapshot[key as keyof MarketSnapshot] !== value,
  );

  if (!changed) {
    return;
  }

  snapshot = {
    ...snapshot,
    ...next,
  };

  emit();
}

export const marketStore = {
  getSnapshot(): MarketSnapshot {
    return snapshot;
  },

  subscribe(listener: Listener) {
    listeners.add(listener);

    return () => {
      listeners.delete(listener);
    };
  },

  setConnectionStatus(
    status: ConnectionStatus,
    error: string | null = null,
  ) {
    update({
      connectionStatus: status,
      connected: status === 'connected',
      error,
    });
  },

  setConnected(value: boolean) {
    this.setConnectionStatus(
      value ? 'connected' : 'disconnected',
      null,
    );
  },

  setPayload(value: MarketPayload) {
    update({
      payload: value,
    });
  },

  setDataSource(value: string) {
    const source = value.trim().toUpperCase();
    if (!source) return;

    // A successful source-switch acknowledgement arrives before the next
    // analytics snapshot. Reflect it immediately so the selector never shows
    // the previous provider while the new source is beginning its poll.
    const current = snapshot.payload ?? {};
    const market =
      current.market && typeof current.market === 'object'
        ? current.market
        : {};

    update({
      payload: {
        ...current,
        dataSource: source,
        market: {
          ...market,
          dataSource: source,
        },
      },
    });
  },

  setError(value: string | null) {
    if (value) {
      this.setConnectionStatus('error', value);
      return;
    }

    update({
      error: null,
    });
  },
};
