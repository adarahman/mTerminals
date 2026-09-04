const WS_URL = "ws://127.0.0.1:5500/ws";

export function connectMarketSocket(
  onMessage: (data: unknown) => void
) {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => console.log("mTerminals connected");

  ws.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch (error) {
      console.error("Invalid WebSocket payload", error);
    }
  };

  ws.onerror = (error) => {
    console.error("WebSocket error", error);
  };

  ws.onclose = () => {
    console.log("mTerminals disconnected");
  };

  return ws;
}
