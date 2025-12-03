// composables/useWebSocket.js
import { ref, onMounted, onUnmounted } from "vue";

const DEFAULT_WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws";

export function useWebSocket(url = DEFAULT_WS_URL) {
  // Connection state
  const ws = ref(null);
  const connected = ref(false);
  const error = ref(null);

  // Real-time Data
  const accountInfo = ref({
    net_liquidation: 0,
    daily_pnl: 0,
  });

  const activePosition = ref(null); // Oggetto singolo o null

  const latestPrice = ref({
    symbol: "---",
    price: 0,
    change_percent: 0,
  });

  const logs = ref([]);
  const systemStatus = ref({});

  // Reconnection logic
  let reconnectInterval = null;
  let reconnectAttempts = 0;
  const maxReconnectAttempts = 100;

  function connect() {
    if (ws.value?.readyState === WebSocket.OPEN) return;

    ws.value = new WebSocket(url);

    ws.value.onopen = () => {
      console.log("✅ WS Connected");
      connected.value = true;
      error.value = null;
      reconnectAttempts = 0;
      if (reconnectInterval) clearInterval(reconnectInterval);

      // Chiedi lo stato iniziale appena connesso
      sendMessage("request-state");
    };

    ws.value.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(data);
      } catch (err) {
        console.error("WS Parse Error", err);
      }
    };

    ws.value.onclose = () => {
      connected.value = false;
      ws.value = null;
      if (!reconnectInterval) startReconnect();
    };

    ws.value.onerror = (e) => {
      console.error("WS Error", e);
    };
  }

  function startReconnect() {
    reconnectInterval = setInterval(() => {
      reconnectAttempts++;
      console.log(`♻️ Reconnecting (${reconnectAttempts})...`);
      connect();
    }, 3000);
  }

  function handleMessage(data) {
    const { type, payload } = data;

    switch (type) {
      case "initial-state":
        // Ripristina tutto lo stato
        if (payload.account) accountInfo.value = payload.account;
        if (payload.active_position) activePosition.value = payload.active_position;
        if (payload.latest_price) latestPrice.value = payload.latest_price;
        if (payload.logs) logs.value = payload.logs;
        break;

      case "price_update":
        // Aggiornamento ultra-rapido
        latestPrice.value = payload;
        // Se abbiamo una posizione attiva sullo stesso simbolo, aggiorniamo il prezzo corrente anche lì
        if (activePosition.value && activePosition.value.symbol === payload.symbol) {
          activePosition.value.current_price = payload.price;
          // Ricalcolo PnL UI-side per fluidità (opzionale)
          activePosition.value.unrealized_pnl = (payload.price - activePosition.value.entry_price) * activePosition.value.shares;
        }
        break;

      case "position_update":
        console.log("📦 Position Update Received:", payload);
        if (Array.isArray(payload)) {
          // Se è una lista (es. []), prendiamo il primo elemento o null
          activePosition.value = payload.length > 0 ? payload[0] : null;
        } else {
          // Se è già un oggetto singolo o null
          activePosition.value = payload;
        }
        break;

      case "account_update":
        accountInfo.value = { ...accountInfo.value, ...payload };
        break;

      case "log":
        logs.value.push({
          ...payload,
          id: Date.now() + Math.random(), // ID univoco per v-for key
          timestamp: payload.timestamp || new Date().toLocaleTimeString(),
        });
        if (logs.value.length > 50) logs.value.shift(); // Tieni solo ultimi 50
        break;
    }
  }

  function sendMessage(type, payload = {}) {
    if (ws.value?.readyState === WebSocket.OPEN) {
      ws.value.send(JSON.stringify({ type, payload }));
    }
  }

  // Commands
  const sendCommand = (cmd, data = {}) => sendMessage("command", { type: cmd, ...data });

  onMounted(() => connect());
  onUnmounted(() => {
    if (ws.value) ws.value.close();
    if (reconnectInterval) clearInterval(reconnectInterval);
  });

  return {
    connected,
    error,
    accountInfo,
    activePosition,
    latestPrice,
    logs,
    sendCommand,
  };
}
