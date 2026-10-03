import { createApp } from 'vue'
import App from './App.vue'
import router from './router'

import 'bootstrap/dist/css/bootstrap.css'
import '@fontsource/ubuntu/400.css'
import '@fontsource/ubuntu/500.css'
import '@fortawesome/fontawesome-free/css/all.css'
import './assets/global.css'

export function createApplication(component = App, applicationRouter = router) {
  const app = createApp(component)
  app.use(applicationRouter)
  return app
}

if (document.querySelector('#app')) {
  createApplication().mount('#app')
}
