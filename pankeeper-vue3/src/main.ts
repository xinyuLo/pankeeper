import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Antd from 'ant-design-vue';
import 'ant-design-vue/dist/reset.css';
import 'dayjs/locale/zh-cn';
import App from './App.vue';
import router from './router';
import './styles/pk.css';
createApp(App).use(createPinia()).use(router).use(Antd).mount('#app');
