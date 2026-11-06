import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles.css'

createApp(App).use(createPinia()).mount('#app')

// PWA（M3）：浏览器模式注册 SW；Electron 内不注册
if ('serviceWorker' in navigator && !window.kairos && location.protocol.startsWith('http')) {
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}
