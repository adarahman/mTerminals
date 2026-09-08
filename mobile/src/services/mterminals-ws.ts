import { marketStore } from '../state/market-store';

let socket: WebSocket | null = null;

let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let sustainedErrorTimer: ReturnType<typeof setTimeout> | null = null;

let activeUrl: string | null = null;
let manuallyDisconnected = false;

const RECONNECT_DELAY_MS = 3000;
const HARD_ERROR_DELAY_MS = 5000;

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function clearSustainedErrorTimer() {
  if (sustainedErrorTimer) {
    clearTimeout(sustainedErrorTimer);
    sustainedErrorTimer = null;
  }
}

function scheduleHardError() {
  if (sustainedErrorTimer) return;

  sustainedErrorTimer = setTimeout(() => {
    sustainedErrorTimer = null;

    if (
      !socket ||
      socket.readyState !== WebSocket.OPEN
    ) {
      marketStore.setConnectionStatus(
        'error',
        'Cannot reach market data. Start Backend in the launcher and check your Wi-Fi connection.',
      );
    }
  }, HARD_ERROR_DELAY_MS);
}

function scheduleReconnect(url: string) {
  clearReconnectTimer();

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;

    if (!manuallyDisconnected) {
      connectMTerminalsWS(url, true);
    }
  }, RECONNECT_DELAY_MS);
}

export function connectMTerminalsWS(
  url: string,
  isReconnect = false,
) {
  activeUrl = url;
  manuallyDisconnected = false;

  if (
    socket &&
    (
      socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING
    )
  ) {
    return;
  }

  marketStore.setConnectionStatus(
    isReconnect ? 'reconnecting' : 'connecting',
    null,
  );

  try {
    const connection = new WebSocket(url);
    socket = connection;

    socket.onopen = () => {
      if (socket !== connection) return;
      console.log('[mobile-ws] connected');

      clearReconnectTimer();
      clearSustainedErrorTimer();

      marketStore.setConnectionStatus(
        'connected',
        null,
      );
    };

    socket.onmessage = (event) => {
      if (socket !== connection) return;
      try {
        const message = JSON.parse(event.data);

        /*
         * Control replies are transport messages, not market snapshots.
         * Never allow them to replace the authoritative live payload.
         */
        if (
          message?.type === 'control_ack' ||
          message?.type === 'control_error'
        ) {
          if (message.type === 'control_error') {
            marketStore.setError(
              String(
                message.message ??
                  message.error ??
                  'Control request failed',
              ),
            );
          }
          return;
        }

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
        console.warn(
          '[mobile-ws] invalid message',
          err,
        );
      }
    };

    socket.onerror = () => {
      if (socket !== connection) return;
      /*
       * Browser/native WebSocket implementations often fire
       * onerror immediately before onclose. Do NOT surface this
       * transient event as a hard user-facing error.
       */
      if (!manuallyDisconnected) {
        marketStore.setConnectionStatus(
          'reconnecting',
          null,
        );

        scheduleHardError();
      }
    };

    socket.onclose = () => {
      if (socket !== connection) return;
      console.log('[mobile-ws] disconnected');

      socket = null;

      if (manuallyDisconnected) {
        clearReconnectTimer();
        clearSustainedErrorTimer();

        marketStore.setConnectionStatus(
          'disconnected',
          null,
        );

        return;
      }

      marketStore.setConnectionStatus(
        'reconnecting',
        null,
      );

      scheduleHardError();
      scheduleReconnect(url);
    };
  } catch (err) {
    socket = null;

    marketStore.setConnectionStatus(
      'reconnecting',
      null,
    );

    scheduleHardError();
    scheduleReconnect(url);

    console.warn(
      '[mobile-ws] connection attempt failed',
      err,
    );
  }
}

export function disconnectMTerminalsWS() {
  manuallyDisconnected = true;

  clearReconnectTimer();
  clearSustainedErrorTimer();

  if (socket) {
    const currentSocket = socket;
    socket = null;

    currentSocket.close();
  }

  marketStore.setConnectionStatus(
    'disconnected',
    null,
  );
}

export function reconnectMTerminalsWS() {
  if (!activeUrl) {
    return;
  }

  manuallyDisconnected = false;

  clearReconnectTimer();
  clearSustainedErrorTimer();

  if (socket) {
    const currentSocket = socket;
    socket = null;
    currentSocket.close();
  }

  connectMTerminalsWS(
    activeUrl,
    true,
  );
}

export function sendMTerminalsWS(
  message: unknown,
) {
  if (
    !socket ||
    socket.readyState !== WebSocket.OPEN
  ) {
    return false;
  }

  socket.send(
    JSON.stringify(message),
  );

  return true;
}
