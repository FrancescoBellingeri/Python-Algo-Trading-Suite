<template>
  <div class="min-h-screen bg-[#0A0E1A] text-gray-100 p-4 md:p-6">
    <div class="max-w-[1600px] mx-auto space-y-6">
      <!-- Header -->
      <header class="flex items-center justify-between">
        <div>
          <h1 class="text-3xl font-bold text-white mb-1 tracking-tight">
            Algo Trading Dashboard
          </h1>
          <p class="text-sm text-[#00D9FF]">Real-time algorithmic trading monitor</p>
        </div>
        <div
          :class="[
            'px-3 py-1 text-sm border-2 rounded-md inline-flex items-center',
            connected
              ? 'border-emerald-500 text-emerald-400 bg-emerald-500/10'
              : 'border-red-500 text-red-400 bg-red-500/10'
          ]"
        >
          <Activity class="w-3 h-3 mr-1" />
          {{ connected ? 'CONNECTED' : 'DISCONNECTED' }}
        </div>
      </header>

      <!-- Stats Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <!-- Current P&L -->
        <div
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-5"
        >
          <div class="flex items-start justify-between mb-3">
            <div class="p-2 rounded-lg bg-[#00D9FF]/10">
              <DollarSign class="w-5 h-5 text-[#00D9FF]" />
            </div>
            <TrendingUp v-if="dailyPnl >= 0" class="w-5 h-5 text-emerald-400" />
            <TrendingDown v-else class="w-5 h-5 text-red-400" />
          </div>
          <div class="space-y-1">
            <p class="text-xs text-gray-400 font-medium uppercase tracking-wide">
              Daily P&L
            </p>
            <p :class="['text-2xl font-bold', dailyPnl >= 0 ? 'text-emerald-400' : 'text-red-400']">
              ${{ dailyPnl.toFixed(2) }}
            </p>
          </div>
        </div>

        <!-- Net Liquidation -->
        <div
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-5"
        >
          <div class="flex items-start justify-between mb-3">
            <div class="p-2 rounded-lg bg-purple-500/10">
              <TrendingUp class="w-5 h-5 text-purple-400" />
            </div>
          </div>
          <div class="space-y-1">
            <p class="text-xs text-gray-400 font-medium uppercase tracking-wide">Net Liquidation</p>
            <p class="text-2xl font-bold text-white">
              ${{ netLiquidation.toFixed(2) }}
            </p>
          </div>
        </div>

        <!-- Positions -->
        <div
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-5"
        >
          <div class="flex items-start justify-between mb-3">
            <div class="p-2 rounded-lg bg-emerald-500/10">
              <Zap class="w-5 h-5 text-emerald-400" />
            </div>
          </div>
          <div class="space-y-1">
            <p class="text-xs text-gray-400 font-medium uppercase tracking-wide">Open Positions</p>
            <p class="text-2xl font-bold text-white">{{ positions.length }}</p>
          </div>
        </div>

        <!-- Buying Power -->
        <div
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-5"
        >
          <div class="flex items-start justify-between mb-3">
            <div class="p-2 rounded-lg bg-amber-500/10">
              <Activity class="w-5 h-5 text-amber-400" />
            </div>
          </div>
          <div class="space-y-1">
            <p class="text-xs text-gray-400 font-medium uppercase tracking-wide">Buying Power</p>
            <p class="text-2xl font-bold text-white">
              ${{ buyingPower.toFixed(2) }}
            </p>
          </div>
        </div>
      </div>

      <!-- Main Content Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- Positions Table - 2 columns -->
        <div
          class="lg:col-span-2 bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-6"
        >
          <div class="mb-4">
            <h2 class="text-lg font-semibold text-white mb-1">Active Positions</h2>
            <p class="text-xs text-gray-400">Current open positions</p>
          </div>
          
          <!-- Positions List -->
          <div v-if="positions.length > 0" class="space-y-2">
            <div v-for="pos in positions" :key="pos.symbol" 
                 class="bg-[#0A0E1A]/50 rounded-lg p-3 border border-[#2A3350]/50">
              <div class="flex justify-between items-center">
                <div>
                  <p class="text-white font-semibold">{{ pos.symbol }}</p>
                  <p class="text-xs text-gray-400">
                    {{ pos.position }} shares @ ${{ pos.avgCost?.toFixed(2) }}
                  </p>
                </div>
                <div class="text-right">
                  <p :class="[
                    'font-semibold',
                    pos.unrealizedPNL >= 0 ? 'text-emerald-400' : 'text-red-400'
                  ]">
                    ${{ dailyPnl?.toFixed(2) || '0.00' }}
                  </p>
                  <p class="text-xs text-gray-400">Unrealized P&L</p>
                </div>
              </div>
            </div>
          </div>
          
          <!-- No Positions -->
          <div v-else 
               class="h-[200px] flex items-center justify-center bg-[#0A0E1A]/50 rounded-lg border border-[#2A3350]/50">
            <div class="text-center space-y-2">
              <Activity class="w-12 h-12 text-gray-600 mx-auto" />
              <p class="text-sm text-gray-500">No open positions</p>
            </div>
          </div>
        </div>

        <!-- Emergency Controls - 1 column -->
        <div
          class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-6"
        >
          <div class="mb-4">
            <h2 class="text-lg font-semibold text-white mb-1">Controls</h2>
            <p class="text-xs text-gray-400">Trading system controls</p>
          </div>
          <div class="space-y-3">
            <button
              @click="handlePause"
              :disabled="!connected"
              class="w-full bg-amber-500 hover:bg-amber-600 text-white font-medium h-12 rounded-md transition-colors duration-200 flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Pause v-if="isRunning" class="w-4 h-4 mr-2" />
              <Play v-else class="w-4 h-4 mr-2" />
              {{ isRunning ? 'Pause Bot' : 'Resume Bot' }}
            </button>

            <button
              @click="handleEmergencyStop"
              :disabled="!connected"
              class="w-full bg-red-500 hover:bg-red-600 text-white font-medium h-12 rounded-md transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
            >
              <AlertTriangle class="w-4 h-4 mr-2" />
              Emergency Stop
            </button>

            <button
              @click="handleForceUpdate"
              :disabled="!connected"
              class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium h-12 rounded-md transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
            >
              <TrendingUp class="w-4 h-4 mr-2" />
              Force Update
            </button>

            <div class="pt-4 mt-4 border-t border-[#2A3350]">
              <div class="space-y-2 text-xs">
                <div class="flex justify-between">
                  <span class="text-gray-400">Bot Status</span>
                  <span :class="[
                    isRunning ? 'text-emerald-400' : 'text-amber-400'
                  ]">
                    {{ botStatus }}
                  </span>
                </div>
                <div class="flex justify-between">
                  <span class="text-gray-400">WebSocket</span>
                  <span :class="[
                    connected ? 'text-emerald-400' : 'text-red-400'
                  ]">
                    {{ connected ? 'Connected' : error || 'Disconnected' }}
                  </span>
                </div>
                <div class="flex justify-between">
                  <span class="text-gray-400">Last Update</span>
                  <span class="text-gray-300">{{ currentTime }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Terminal Logs -->
      <div
        class="bg-gradient-to-br from-[#1A1F35] to-[#0F1421] border border-[#2A3350] rounded-lg p-6"
      >
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold text-white mb-1">Live Terminal</h2>
            <p class="text-xs text-gray-400">Real-time trading activity logs</p>
          </div>
          <button
            @click="clearLogs"
            class="text-xs text-gray-400 hover:text-white px-3 py-1 rounded hover:bg-white/5 transition-colors"
          >
            Clear Logs
          </button>
        </div>

        <div
          ref="logContainer"
          class="bg-[#0A0E1A] rounded-lg p-4 h-[300px] overflow-y-auto font-mono text-xs space-y-1 custom-scrollbar"
        >
          <p v-if="logs.length === 0" class="text-gray-600">Waiting for trading signals...</p>
          <div v-else v-for="log in logs" :key="log.id" class="flex gap-3 py-1">
            <span class="text-gray-500 flex-shrink-0">[{{ log.timestamp }}]</span>
            <span
              :class="{
                'text-emerald-400': log.level === 'success',
                'text-red-400': log.level === 'error',
                'text-amber-400': log.level === 'warning',
                'text-[#00D9FF]': log.level === 'info',
                'text-gray-400': log.level === 'debug'
              }"
                          >
              {{ log.message }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import {
  Activity,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Zap,
  AlertTriangle,
  Pause,
  Play
} from 'lucide-vue-next'

// WebSocket connection
const {
  connected,
  error,
  accountInfo,
  positions,
  pnl,
  logs,
  systemStatus,
  pauseBot,
  resumeBot,
  stopBot,
  forceUpdate
} = useWebSocket()

// Local state
const logContainer = ref(null)
const isRunning = ref(false)
const botStatus = ref('Unknown')

// Computed values from WebSocket data
const dailyPnl = computed(() => pnl.value?.daily_pnl || 0)
const netLiquidation = computed(() => accountInfo.value?.net_liquidation || 0)
const buyingPower = computed(() => accountInfo.value?.buying_power || 0)
const currentTime = computed(() => new Date().toLocaleTimeString())

// Watch for system status changes
watch(systemStatus, (newStatus) => {
  if (newStatus) {
    isRunning.value = newStatus.bot_status === 'running'
    botStatus.value = newStatus.bot_status?.toUpperCase() || 'UNKNOWN'
  }
}, { deep: true })

// Auto-scroll logs
watch(logs, async () => {
  await nextTick()
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, { deep: true })

// Control handlers
const handlePause = async () => {
  if (isRunning.value) {
    await pauseBot()
    isRunning.value = false
    botStatus.value = 'PAUSED'
  } else {
    await resumeBot()
    isRunning.value = true
    botStatus.value = 'RUNNING'
  }
}

const handleEmergencyStop = async () => {
  if (confirm('Are you sure you want to execute an emergency stop? This will stop the bot completely.')) {
    await stopBot()
    isRunning.value = false
    botStatus.value = 'STOPPED'
  }
}

const handleForceUpdate = async () => {
  await forceUpdate()
}

const clearLogs = () => {
  logs.value = []
}

// Update timer for current time
let timeInterval = null

onMounted(() => {
  // Force time update every second
  timeInterval = setInterval(() => {
    // This will trigger the computed property to update
  }, 1000)
})

onUnmounted(() => {
  if (timeInterval) {
    clearInterval(timeInterval)
  }
})
</script>

<style scoped>
/* Custom scrollbar styling for terminal logs */
.custom-scrollbar::-webkit-scrollbar {
  width: 8px;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: rgba(42, 51, 80, 0.3);
  border-radius: 4px;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(0, 217, 255, 0.3);
  border-radius: 4px;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 217, 255, 0.5);
}

/* Firefox scrollbar */
.custom-scrollbar {
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 217, 255, 0.3) rgba(42, 51, 80, 0.3);
}
</style>