import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/store/auth';
import BasicLayout from '@/layouts/BasicLayout.vue';
export const AUTO_TYPES = ['baidu', 'quark', '115'] as const;
export type AutoType = (typeof AUTO_TYPES)[number];
export const AUTO_TITLE: Record<AutoType, string> = {
    baidu: '百度自动转存',
    quark: '夸克自动转存',
    '115': '115 自动转存',
};
const routes: RouteRecordRaw[] = [
    {
        path: '/login',
        name: 'login',
        component: () => import('@/views/login/Login.vue'),
        meta: { title: '登录' },
    },
    {
        path: '/',
        component: BasicLayout,
        redirect: '/dashboard',
        children: [
            {
                path: 'dashboard',
                name: 'dashboard',
                component: () => import('@/views/dashboard/Dashboard.vue'),
                meta: { title: '首页' },
            },
            {
                path: 'search',
                name: 'search',
                component: () => import('@/views/search/SearchTransfer.vue'),
                meta: { title: '搜索转存' },
            },
            {
                path: 'records',
                name: 'records',
                component: () => import('@/views/records/Records.vue'),
                meta: { title: '搜索历史' },
            },
            {
                path: 'default-dir',
                name: 'default-dir',
                component: () => import('@/views/defaultdir/DefaultDir.vue'),
                meta: { title: '转存配置' },
            },
            {
                path: 'auto/:type(baidu|quark|115)',
                name: 'auto',
                component: () => import('@/views/auto/AutoTransfer.vue'),
                meta: { title: '自动转存' },
            },
            {
                path: 'auto/history',
                name: 'auto-history',
                component: () => import('@/views/auto/RunHistoryPage.vue'),
                meta: { title: '转存历史' },
            },
            {
                path: 'accounts',
                name: 'accounts',
                component: () => import('@/views/accounts/Accounts.vue'),
                meta: { title: '网盘连接' },
            },
            {
                path: 'drive-logs',
                name: 'drive-logs',
                component: () => import('@/views/logs/DriveLogs.vue'),
                meta: { title: '请求日志' },
            },
            {
                path: 'push-logs',
                name: 'push-logs',
                component: () => import('@/views/logs/PushLogs.vue'),
                meta: { title: '推送历史' },
            },
            {
                path: 'cache-config',
                name: 'cache-config',
                component: () => import('@/views/cachecfg/CacheConfig.vue'),
                meta: { title: '缓存配置' },
            },
            {
                path: 'queue-config',
                name: 'queue-config',
                component: () => import('@/views/queuecfg/QueueConfig.vue'),
                meta: { title: '队列配置' },
            },
            {
                path: 'settings',
                name: 'settings',
                component: () => import('@/views/settings/Settings.vue'),
                meta: { title: '系统设置' },
            },
        ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
];
const router = createRouter({
    history: createWebHistory(),
    routes,
});
router.beforeEach((to) => {
    const auth = useAuthStore();
    if (to.name !== 'login' && !auth.logged)
        return { name: 'login' };
    if (to.name === 'login' && auth.logged)
        return { name: 'dashboard' };
    return true;
});
router.afterEach((to) => {
    const base = to.meta.title as string;
    if (to.name === 'auto') {
        const t = to.params.type as AutoType;
        document.title = (AUTO_TITLE[t] || base) + ' · PanKeeper';
    }
    else {
        document.title = (base || '') + ' · PanKeeper';
    }
});
export default router;
