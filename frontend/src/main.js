import { createApp } from 'vue'
import './styles/shared.css'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { installApiAuth } from './utils/apiAuth'

installApiAuth()   // must run before any request or stream is made

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
