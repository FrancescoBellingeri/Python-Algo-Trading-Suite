<template>
  <div class="tradingview-chart-container bg-[#131722] border border-[#2A3350] rounded-lg overflow-hidden">
    <div class="p-3 border-b border-[#2A3350] flex justify-between items-center bg-[#0A0E1A]">
      <div class="flex items-center gap-2">
        <TrendingUp class="text-[#00D9FF] w-4 h-4" />
        <span class="text-xs font-bold text-gray-300 uppercase">{{ symbol }} Chart</span>
      </div>
      <span class="text-xs text-gray-500">{{ interval }} Timeframe</span>
    </div>
    <div ref="chartContainer" class="tradingview-widget-container"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onUnmounted } from 'vue'
import { TrendingUp } from 'lucide-vue-next'

const props = defineProps({
  symbol: { type: String, default: 'NASDAQ:QQQ' },
  interval: { type: String, default: '5' },
  height: { type: Number, default: 500 },
  theme: { type: String, default: 'dark' }
})

const chartContainer = ref(null)
let scriptId = null

const createWidget = () => {
  if (chartContainer.value) {
    chartContainer.value.innerHTML = ''
    const widgetId = `tradingview_${Date.now()}`
    const innerDiv = document.createElement('div')
    innerDiv.id = widgetId
    innerDiv.style.height = `${props.height}px`
    chartContainer.value.appendChild(innerDiv)

    const script = document.createElement('script')
    script.type = 'text/javascript'
    script.async = true
    script.src = 'https://s3.tradingview.com/tv.js'
    scriptId = `tv_script_${Date.now()}`
    script.id = scriptId
    
    script.onload = () => {
      if (window.TradingView) {
        new window.TradingView.widget({
          container_id: widgetId,
          autosize: true,
          symbol: props.symbol,
          interval: props.interval,
          timezone: 'Europe/Rome',
          theme: props.theme,
          style: '1', // Candlestick
          locale: 'it',
          toolbar_bg: '#0A0E1A',
          enable_publishing: false,
          allow_symbol_change: true,
          hide_side_toolbar: false,
          withdateranges: true,
          save_image: true,
          hide_legend: false,
          // Indicatori: SMA 200 e Williams %R 10
          studies: [
            { id: 'MASimple@tv-basicstudies', inputs: { length: 200 } },
            // { id: 'Williams Percent Range@tv-basicstudies', inputs: { length: 10 } }
          ]
        })
      }
    }
    document.head.appendChild(script)
  }
}

onMounted(() => createWidget())
watch(() => [props.symbol, props.interval], () => createWidget())
onUnmounted(() => {
  if (scriptId) {
    const script = document.getElementById(scriptId)
    if (script) script.remove()
  }
})
</script>

<style scoped>
.tradingview-chart-container {
  min-height: 400px;
}

.tradingview-widget-container {
  width: 100%;
  height: 100%;
}

:deep(.tradingview-widget-container) iframe {
  width: 100% !important;
}
</style>