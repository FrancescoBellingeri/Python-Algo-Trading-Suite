// composables/useWebSocket.js
import { ref, onMounted, onUnmounted } from "vue";

const DEFAULT_WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws";

export function useWebSocket(url = DEFAULT_WS_URL) {
  // Connection state
  const ws = ref(null);
  const connected = ref(false);
  const error = ref(null);

  // Trading data
  const accountInfo = ref({});
  const positions = ref([]);
  const pnl = ref({});
  const logs = ref([]);
  const systemStatus = ref({});

  // Reconnection settings
  let reconnectInterval = null;
  let reconnectAttempts = 0;
  const maxReconnectAttempts = 10;
  const reconnectDelay = 3000;

  // Connect function
  function connect() {
    try {
      if (ws.value?.readyState === WebSocket.OPEN) return;

      console.log("🔌 Connecting to WebSocket...");
      ws.value = new WebSocket(url);

      ws.value.onopen = () => {
        console.log("✅ WebSocket connected");
        connected.value = true;
        error.value = null;
        reconnectAttempts = 0;

        if (reconnectInterval) {
          clearInterval(reconnectInterval);
          reconnectInterval = null;
        }

        // Request initial state
        sendMessage("request-state");
      };

      ws.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleMessage(data);
        } catch (err) {
          console.error("Error parsing message:", err);
        }
      };

      ws.value.onerror = (err) => {
        console.error("❌ WebSocket error:", err);
        error.value = "Connection error";
      };

      ws.value.onclose = () => {
        console.log("🔴 WebSocket disconnected");
        connected.value = false;
        ws.value = null;
        startReconnect();
      };
    } catch (err) {
      console.error("Failed to create WebSocket:", err);
      error.value = err.message;
      startReconnect();
    }
  }

  // Auto reconnect
  function startReconnect() {
    if (reconnectInterval) return;

    if (reconnectAttempts >= maxReconnectAttempts) {
      error.value = "Max reconnection attempts reached";
      return;
    }

    reconnectInterval = setInterval(() => {
      reconnectAttempts++;
      console.log(`🔄 Reconnect attempt ${reconnectAttempts}/${maxReconnectAttempts}...`);
      connect();
    }, reconnectDelay);
  }

  // Handle messages
  function handleMessage(data) {
    const { type, payload } = data;

    switch (type) {
      case "initial-state":
        if (payload.account_info) accountInfo.value = payload.account_info;
        if (payload.positions) positions.value = payload.positions;
        if (payload.pnl) pnl.value = payload.pnl;
        if (payload.logs) logs.value = payload.logs.slice(-100);
        break;

      case "account-update":
        accountInfo.value = { ...accountInfo.value, ...payload };
        break;

      case "position-update":
      case "positions-update":
        positions.value = Array.isArray(payload) ? payload : [payload];
        break;

      case "pnl-update":
        pnl.value = payload;
        break;

      case "system-status":
        systemStatus.value = payload;
        break;

      case "bot-status":
        systemStatus.value = { ...systemStatus.value, ...payload };
        break;

      case "log":
        logs.value.push({
          ...payload,
          id: Date.now() + Math.random(),
          timestamp: payload.timestamp || new Date().toLocaleTimeString(),
        });
        if (logs.value.length > 100) {
          logs.value = logs.value.slice(-100);
        }
        break;

      case "error":
        error.value = payload.message;
        logs.value.push({
          id: Date.now(),
          timestamp: new Date().toLocaleTimeString(),
          level: "error",
          message: payload.message || "Unknown error",
        });
        break;
    }
  }

  // Send message
  function sendMessage(type, payload = {}) {
    if (ws.value?.readyState === WebSocket.OPEN) {
      ws.value.send(
        JSON.stringify({
          type,
          payload,
          timestamp: new Date().toISOString(),
        })
      );
      return true;
    }
    return false;
  }

  // Bot control commands
  const pauseBot = () => sendMessage("command", { type: "pause" });
  const resumeBot = () => sendMessage("command", { type: "resume" });
  const stopBot = () => sendMessage("command", { type: "stop" });
  const forceUpdate = () => sendMessage("command", { type: "force_update" });

  // Disconnect
  function disconnect() {
    if (reconnectInterval) {
      clearInterval(reconnectInterval);
      reconnectInterval = null;
    }

    if (ws.value) {
      ws.value.close();
      ws.value = null;
    }

    connected.value = false;
  }

  // Lifecycle
  onMounted(() => {
    connect();
  });

  onUnmounted(() => {
    disconnect();
  });

  return {
    // State
    connected,
    error,

    // Data
    accountInfo,
    positions,
    pnl,
    logs,
    systemStatus,

    // Methods
    connect,
    disconnect,
    sendMessage,

    // Bot controls
    pauseBot,
    resumeBot,
    stopBot,
    forceUpdate,
  };
}
