<template>
  <div class="min-h-screen bg-[#0A0E1A] text-gray-100 p-4 md:p-6 font-sans">
    <div class="max-w-[1800px] mx-auto space-y-6">

      <!-- HEADER -->
      <header class="flex items-center justify-between border-b border-[#2A3350] pb-4">
        <div>
          <h1 class="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <Activity class="text-[#00D9FF]" />
            Algo Trading Dashboard
          </h1>
          <p class="text-xs text-gray-400 mt-1">Real-time algorithmic trading monitor</p>
        </div>

        <div class="flex items-center gap-4">
          <!-- Connection Status -->
          <div
            :class="['px-3 py-1 text-xs font-bold rounded-full border',
              connected ? 'bg-emerald-500/10 border-emerald-500 text-emerald-400' : 'bg-red-500/10 border-red-500 text-red-400']">
            {{ connected ? 'LIVE FEED' : 'OFFLINE' }}
          </div>
        </div>
      </header>

      <!-- ========== PRIMARY SECTION: CHART + LOGS ========== -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        <!-- TRADINGVIEW QQQ CHART (Main Focus - 2/3 width) -->
        <div class="lg:col-span-2">
          <TradingViewChart 
            symbol="NASDAQ:QQQ" 
            interval="5" 
            :height="550"
            theme="dark"
          />
        </div>

        <!-- SYSTEM LOGS (Live WS) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg flex flex-col h-[590px]">
          <div class="p-3 border-b border-[#2A3350] flex justify-between items-center bg-[#0A0E1A]">
            <span class="text-xs font-bold text-gray-300 uppercase">Live Terminal</span>
            <div class="flex gap-2">
              <div class="w-2 h-2 rounded-full bg-red-500 animate-pulse" v-if="connected"></div>
            </div>
          </div>

          <div ref="logsContainer" class="flex-1 overflow-y-auto p-3 font-mono text-xs space-y-1 custom-scrollbar">
            <div v-for="log in logs" :key="log.id" class="leading-relaxed break-all">
              <span class="text-gray-600 mr-2">[{{ log.timestamp }}]</span>
              <span :class="{
                'text-[#00D9FF]': log.level === 'info',
                'text-amber-400': log.level === 'warning',
                'text-red-400': log.level === 'error',
                'text-emerald-400': log.level === 'success',
                'text-gray-400': log.level === 'debug'
              }">{{ log.message }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- ========== SECONDARY SECTION: KPI CARDS + CONTROLS ========== -->
      <div class="grid grid-cols-1 md:grid-cols-5 gap-4">
        <!-- 1. Net Liquidation (WS) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg p-4">
          <p class="text-gray-400 text-xs uppercase font-semibold">Net Liquidation</p>
          <p class="text-2xl font-bold text-white mt-1">${{ formatMoney(accountInfo.net_liquidation) }}</p>
        </div>

        <!-- 2. Max Drawdown (API Stats) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg p-4">
          <p class="text-gray-400 text-xs uppercase font-semibold">Max Drawdown</p>
          <p :class="['text-2xl font-bold mt-1', stats.max_drawdown_dollar ? 'text-red-400' : 'text-gray-400']">
            {{ stats.max_drawdown_dollar ? '-' : '' }}${{ formatMoney(stats.max_drawdown_dollar) }}
          </p>
          <!-- Depth and percent come off the same slide, so naming the peak they
               fell from makes the pair checkable at a glance. -->
          <p v-if="stats.max_drawdown_peak_equity" class="text-xs text-gray-500">
            -{{ formatMoney(stats.max_drawdown_percent) }}% from peak ${{ formatMoney(stats.max_drawdown_peak_equity) }}
          </p>
          <p v-else-if="stats.max_drawdown_dollar" class="text-xs text-amber-500/70">
            peak-to-trough &middot; set STARTING_EQUITY for %
          </p>
          <p v-else class="text-xs text-gray-500">no drawdown yet</p>
        </div>

        <!-- 3. Win Rate (API Stats) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg p-4">
          <p class="text-gray-400 text-xs uppercase font-semibold">Win Rate</p>
          <p class="text-2xl font-bold text-[#00D9FF] mt-1">{{ stats.win_rate_percent }}%</p>
          <p class="text-xs text-gray-500">{{ stats.total_trades }} Trades Total</p>
        </div>

        <!-- 4. Total PnL (API Stats) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg p-4">
          <p class="text-gray-400 text-xs uppercase font-semibold">Total Profit</p>
          <p :class="['text-2xl font-bold mt-1', getPnlColor(stats.total_pnl_dollar)]">
            ${{ formatMoney(stats.total_pnl_dollar) }}
          </p>
        </div>

        <!-- BOT CONTROLS (inline) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg p-4">
          <p class="text-gray-400 text-xs uppercase font-semibold mb-2">Controls</p>
          <div class="grid grid-cols-2 gap-2">
            <button @click="sendCommand('stop')"
              class="bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/50 p-2 rounded transition flex items-center justify-center gap-1">
              <AlertTriangle class="w-3 h-3" />
              <span class="text-[10px] font-bold">STOP</span>
            </button>
            <button @click="sendCommand('close_positions')"
              class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/50 p-2 rounded transition flex items-center justify-center gap-1">
              <XCircle class="w-3 h-3" />
              <span class="text-[10px] font-bold">CLOSE</span>
            </button>
          </div>
        </div>
      </div>

      <!-- ========== TERTIARY SECTION: POSITION + HISTORY ========== -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        
        <!-- LIVE ACTIVE POSITION CARD -->
        <div v-if="activePosition && activePosition.symbol"
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#00D9FF]/30 rounded-xl p-6 relative overflow-hidden">
          <!-- Background pulse effect -->
          <div class="absolute top-0 right-0 w-32 h-32 bg-[#00D9FF]/5 rounded-full blur-3xl -mr-10 -mt-10"></div>

          <div class="flex justify-between items-start mb-6">
            <div>
              <div class="flex items-center gap-3">
                <h2 class="text-3xl font-bold text-white">{{ activePosition.symbol }}</h2>
                <span class="px-2 py-1 bg-[#00D9FF]/20 text-[#00D9FF] text-xs font-bold rounded">LONG</span>
              </div>
              <p class="text-gray-400 text-sm mt-1">
                {{ activePosition.shares }} shares @ ${{ activePosition.entry_price?.toFixed(2) }}
              </p>
            </div>
            <div class="text-right">
              <p class="text-sm text-gray-400 uppercase">Unrealized P&L</p>
              <p :class="['text-4xl font-bold tracking-tighter', getPnlColor(activePosition.unrealized_pnl)]">
                ${{ formatMoney(activePosition.unrealized_pnl) }}
              </p>
            </div>
          </div>

          <!-- Strategy Monitors Grid -->
          <div class="grid grid-cols-3 gap-4">
            <!-- Current Price -->
            <div class="bg-[#0A0E1A]/50 p-3 rounded border border-gray-700">
              <p class="text-xs text-gray-500 mb-1">Current Price</p>
              <p class="text-xl font-mono text-white">${{ activePosition.current_price?.toFixed(2) }}</p>
            </div>

            <!-- Trailing Stop Monitor -->
            <div class="bg-[#0A0E1A]/50 p-3 rounded border border-gray-700 relative">
              <p class="text-xs text-gray-500 mb-1">Trailing Stop</p>
              <p class="text-xl font-mono text-amber-400">${{ activePosition.current_trailing_stop?.toFixed(2) ||
                '---' }}</p>
            </div>

            <!-- SMA Monitor -->
            <div class="bg-[#0A0E1A]/50 p-3 rounded border border-gray-700">
              <p class="text-xs text-gray-500 mb-1">SMA Value</p>
              <p class="text-xl font-mono text-purple-400">${{ activePosition.current_sma_value?.toFixed(2) || '---'
                }}</p>
            </div>
          </div>
        </div>

        <!-- NO POSITION / SCANNING STATE -->
        <div v-else
          class="bg-[#131722] border border-[#2A3350] border-dashed rounded-xl p-8 flex flex-col items-center justify-center text-center">
          <div class="w-14 h-14 bg-[#0A0E1A] rounded-full flex items-center justify-center mb-4 relative">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00D9FF] opacity-20"></span>
            <Activity class="text-[#00D9FF] w-7 h-7" />
          </div>
          <h3 class="text-lg font-semibold text-white">Scanning Market...</h3>
          <p class="text-gray-500 mt-2 max-w-md text-sm">Monitoring <span class="text-[#00D9FF] font-mono">{{
            latestPrice.symbol || '---' }}</span> (${{ latestPrice.price.toFixed(2) }}) for signals.
          </p>
        </div>

        <!-- RECENT HISTORY TABLE (From API) -->
        <div class="bg-[#131722] border border-[#2A3350] rounded-lg overflow-hidden">
          <div class="p-4 border-b border-[#2A3350] flex justify-between items-center">
            <h3 class="font-semibold text-white">Recent Trade History</h3>
            <button @click="fetchHistory" class="text-xs text-[#00D9FF] hover:text-white transition-colors">
              Refresh
            </button>
          </div>

          <div class="overflow-x-auto max-h-[280px]">
            <table class="w-full text-left text-sm text-gray-400">
              <thead class="bg-[#0A0E1A] text-xs uppercase font-medium sticky top-0">
                <tr>
                  <th class="px-4 py-3">Symbol</th>
                  <th class="px-4 py-3">Date</th>
                  <th class="px-4 py-3 text-right">Qty</th>
                  <th class="px-4 py-3 text-right">Entry</th>
                  <th class="px-4 py-3 text-right">Exit</th>
                  <th class="px-4 py-3 text-right">PnL $</th>
                  <th class="px-4 py-3 text-center">Reason</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-[#2A3350]">
                <tr v-for="trade in tradeHistory" :key="trade.id" class="hover:bg-[#1A1F35] transition-colors">
                  <td class="px-4 py-3 font-medium text-white">{{ trade.symbol }}</td>
                  <td class="px-4 py-3 text-xs whitespace-nowrap">{{ formatDate(trade.exit_time) }}</td>
                  <td class="px-4 py-3 text-right">{{ trade.quantity }}</td>
                  <td class="px-4 py-3 text-right font-mono text-xs text-gray-300">${{ trade.entry_price.toFixed(2) }}</td>
                  <td class="px-4 py-3 text-right font-mono text-xs text-gray-300">${{ trade.exit_price.toFixed(2) }}</td>
                  <td :class="['px-4 py-3 text-right font-bold', getPnlColor(trade.pnl_dollar)]">
                    ${{ trade.pnl_dollar.toFixed(2) }}
                  </td>
                  <td class="px-4 py-3 text-center">
                    <span class="px-2 py-0.5 rounded text-[9px] font-bold bg-gray-700/50 text-gray-400 border border-gray-600/30 whitespace-nowrap">
                      {{ trade.exit_reason }}
                    </span>
                  </td>
                </tr>
                <tr v-if="tradeHistory.length === 0">
                  <td colspan="5" class="px-4 py-8 text-center text-gray-600">No trades recorded yet.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
  import { ref, onMounted, nextTick, watch } from 'vue'
  import { useWebSocket } from '@/composables/useWebSocket'
  import { Activity, AlertTriangle, XCircle } from 'lucide-vue-next'
  import TradingViewChart from './TradingViewChart.vue'

  // --- COMPOSABLE (WebSocket Data) ---
  const {
    connected,
    accountInfo,
    activePosition,
    latestPrice,
    logs,
    sendCommand
  } = useWebSocket()

  // --- LOCAL STATE (API Data) ---
  const tradeHistory = ref([])
  const stats = ref({
    total_trades: 0,
    win_rate_percent: 0,
    total_pnl_dollar: 0,
    max_drawdown_dollar: 0,
    max_drawdown_percent: 0,
    max_drawdown_peak_equity: null,
    starting_equity: null,
  })

  const logsContainer = ref(null)

  // --- API METHODS ---
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

  const fetchHistory = async () => {
    try {
      const res = await fetch(`${API_URL}/api/history?limit=10`)
      const json = await res.json()
      tradeHistory.value = json.data
    } catch (e) {
      console.error("Failed to fetch history", e)
    }
  }

  const fetchStats = async () => {
    try {
      const res = await fetch(`${API_URL}/api/stats`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      // Keep the last good values if the endpoint answers with nothing usable,
      // rather than assigning null and blanking every KPI card.
      if (json && typeof json === 'object') stats.value = json
      else console.error("Stats endpoint returned no data")
    } catch (e) {
      console.error("Failed to fetch stats", e)
    }
  }

  // --- UTILS ---
  const formatMoney = (val) => (val || 0).toFixed(2)

  const getPnlColor = (val) => {
    if (!val) return 'text-gray-400'
    return val >= 0 ? 'text-emerald-400' : 'text-red-400'
  }

  const formatDate = (isoString) => {
    if (!isoString) return ''
    return new Date(isoString).toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    })
  }

  // --- LIFECYCLE & WATCHERS ---

  // Auto-scroll logs
  watch(logs, async () => {
    await nextTick()
    if (logsContainer.value) {
      logsContainer.value.scrollTop = logsContainer.value.scrollHeight
    }
  }, { deep: true })

  onMounted(() => {
    // Load initial "Cold Data"
    fetchHistory()
    fetchStats()

    // Refresh stats periodically (every 1 min) just in case
    setInterval(() => {
      fetchStats()
      fetchHistory() // Update table if a trade closed
    }, 60000)
  })

</script>

<style scoped>

  /* Scrollbar Customization */
  .custom-scrollbar::-webkit-scrollbar {
    width: 6px;
  }

  .custom-scrollbar::-webkit-scrollbar-track {
    background: #0A0E1A;
  }

  .custom-scrollbar::-webkit-scrollbar-thumb {
    background: #2A3350;
    border-radius: 3px;
  }

  .custom-scrollbar::-webkit-scrollbar-thumb:hover {
    background: #3B4768;
  }
</style>