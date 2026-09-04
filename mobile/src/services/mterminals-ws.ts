import { marketStore } from '../state/market-store';

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export function connectMTerminalsWS(url: string) {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  marketStore.setError(null);

  try {
    socket = new WebSocket(url);

    socket.onopen = () => {
      console.log('[mobile-ws] connected:', url);
      marketStore.setConnected(true);
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);

        /*
         * Accept both:
         *   1. direct market payload
         *   2. envelopes such as { type, payload: {...} }
         */
        const nextPayload =
          message &&
          typeof message === 'object' &&
          message.payload &&
          typeof message.payload === 'object'
            ? message.payload
            : message;

        marketStore.setPayload(nextPayload);
      } catch (err) {
        console.warn('[mobile-ws] invalid message', err);
      }
    };

    socket.onerror = () => {
      marketStore.setError('WebSocket connection error');
    };

    socket.onclose = () => {
      console.log('[mobile-ws] disconnected');
      marketStore.setConnected(false);
      socket = null;

      if (reconnectTimer) clearTimeout(reconnectTimer);

      reconnectTimer = setTimeout(() => {
        connectMTerminalsWS(url);
      }, 3000);
    };
  } catch (err) {
    marketStore.setConnected(false);
    marketStore.setError(
      err instanceof Error ? err.message : 'Unable to connect',
    );
  }
}

export function disconnectMTerminalsWS() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  socket?.close();
  socket = null;
}

export function sendMTerminalsWS(message: unknown) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return false;
  }

  socket.send(JSON.stringify(message));
  return true;
}
