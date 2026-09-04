import { useEffect, useSyncExternalStore } from 'react';

import { connectMTerminalsWS } from '../services/mterminals-ws';
import { marketStore } from '../state/market-store';

const WS_URL =
  process.env.EXPO_PUBLIC_MTERMINALS_WS ??
  'ws://localhost:5500/ws';

export function useMarketData() {
  const snapshot = useSyncExternalStore(
    marketStore.subscribe,
    marketStore.getSnapshot,
    marketStore.getSnapshot,
  );

  useEffect(() => {
    connectMTerminalsWS(WS_URL);
  }, []);



  return snapshot;
}
