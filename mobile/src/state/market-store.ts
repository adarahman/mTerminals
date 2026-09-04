export type MarketPayload = Record<string, any>;

type Listener = () => void;

type MarketSnapshot = {
  payload: MarketPayload | null;
  connected: boolean;
  error: string | null;
};

const listeners = new Set<Listener>();

let snapshot: MarketSnapshot = {
  payload: null,
  connected: false,
  error: null,
};

function emit() {
  for (const listener of listeners) {
    listener();
  }
}

function update(next: Partial<MarketSnapshot>) {
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

  setConnected(value: boolean) {
    if (
      snapshot.connected === value &&
      !(value && snapshot.error !== null)
    ) {
      return;
    }

    update({
      connected: value,
      ...(value ? { error: null } : {}),
    });
  },

  setPayload(value: MarketPayload) {
    update({
      payload: value,
    });
  },

  setError(value: string | null) {
    if (snapshot.error === value) {
      return;
    }

    update({
      error: value,
    });
  },
};
