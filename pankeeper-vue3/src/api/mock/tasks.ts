import { reactive } from 'vue'
import { USE_MOCK } from '../http'
import type { PaTask } from '@/types/model'

/**
 * 自动转存任务 mock —— 原型 parts/page-auto.html 数据原样移植。
 * 驾驶舱 / 自动转存三页共用，内存态（会话内可变）。
 */
export const paStore = reactive<{ tasks: PaTask[]; seq: number }>({
  // 真实模式初始为空：数据一律来自后端 hydrate（登录前引导会 401，别残留假数据）
  tasks: USE_MOCK ? [
    {
      id: 1, type: 'baidu', name: '兰香如故', enabled: true,
      share_url: 'https://pan.baidu.com/s/1aBcDeFgHiJkLmNoPqRs', share_code: 'abcd',
      save_dir: '/影视/国产剧/兰香如故', compare_path: '/影视/国产剧/兰香如故', include_subdirs: true,
      cron: '0 3 * * *', exclude_count: 3, exclIdx: [], last_run: '09-26 03:00', last_status: 'success',
      last_result: '新增 2 / 跳过 34 / 失败 0', post_qms: true, post_notify: true,
    },
    {
      id: 2, type: 'baidu', name: '长安的荔枝', enabled: false,
      share_url: 'https://pan.baidu.com/s/1xYzWvUtSrQpOnMlKjIh', share_code: 'qwer',
      save_dir: '/影视/国产剧/长安的荔枝', compare_path: '/影视/国产剧/长安的荔枝', include_subdirs: true,
      cron: '0 4 * * *', exclude_count: 0, exclIdx: [], last_run: '09-25 04:00', last_status: 'never',
      last_result: '—', post_qms: true, post_notify: false,
    },
    {
      id: 3, type: 'baidu', name: '繁花', enabled: true,
      share_url: 'https://pan.baidu.com/s/1ZxCvBnMkJiHgFeDcBaA', share_code: 'zxcv',
      save_dir: '/影视/国产剧/繁花', compare_path: '/影视/国产剧/繁花', include_subdirs: true,
      cron: '30 2 * * *', exclude_count: 1, exclIdx: [], last_run: '09-26 02:30', last_status: 'fail',
      last_result: '新增 0 / 跳过 5 / 失败 3', post_qms: true, post_notify: true,
    },
    {
      id: 4, type: 'quark', name: '九重紫 每日中午检查', enabled: true,
      share_url: 'https://pan.quark.cn/s/2AbCdEfGhIjKlMn', share_code: 'qk12',
      save_dir: '/夸克影视/九重紫', compare_path: '/夸克影视/九重紫', include_subdirs: true,
      cron: '0 12 * * *', exclude_count: 2, exclIdx: [], last_run: '09-26 12:00', last_status: 'success',
      last_result: '新增 1 / 跳过 12 / 失败 0', post_qms: true, post_notify: true,
    },
    {
      id: 5, type: 'quark', name: '小巷人家', enabled: true,
      share_url: 'https://pan.quark.cn/s/3GhIjKlMnOpQrS', share_code: '',
      save_dir: '/夸克影视/小巷人家', compare_path: '/夸克影视/小巷人家', include_subdirs: false,
      cron: '0 1 * * *', exclude_count: 0, exclIdx: [], last_run: '09-26 01:00', last_status: 'running',
      last_result: '执行中…', post_qms: false, post_notify: true,
    },
    {
      id: 6, type: '115', name: '玫瑰的故事', enabled: true,
      share_url: 'https://115.com/s/4MnOpQrStUvWxY', share_code: '115a',
      save_dir: '/115影视/玫瑰的故事', compare_path: '/115影视/玫瑰的故事', include_subdirs: true,
      cron: '0 23 * * *', exclude_count: 5, exclIdx: [], last_run: '09-25 23:00', last_status: 'success',
      last_result: '新增 4 / 跳过 20 / 失败 0', post_qms: true, post_notify: true,
    },
    {
      id: 7, type: '115', name: '与凤行（仅手动）', enabled: false,
      share_url: 'https://115.com/s/5StUvWxYzAbCdE', share_code: '115b',
      save_dir: '/115影视/与凤行', compare_path: '/115影视/与凤行', include_subdirs: true,
      cron: '', exclude_count: 0, exclIdx: [], last_run: '09-20 10:00', last_status: 'never',
      last_result: '—', post_qms: true, post_notify: true,
    },
    {
      id: 8, type: '115', name: '庆余年2', enabled: true,
      share_url: 'https://115.com/s/6YzAbCdEfGhIjK', share_code: '115c',
      save_dir: '/115影视/庆余年2', compare_path: '/115影视/庆余年2', include_subdirs: true,
      cron: '0 3 * * *', exclude_count: 1, exclIdx: [], last_run: '09-26 03:00', last_status: 'fail',
      last_result: '新增 0 / 跳过 8 / 失败 2', post_qms: true, post_notify: true,
    },
  ] : [],
  seq: 100,
})

export function paGetById(id: number): PaTask | null {
  return paStore.tasks.find((t) => t.id === id) || null
}

export function paByType(type: string): PaTask[] {
  return paStore.tasks.filter((t) => t.type === type)
}
