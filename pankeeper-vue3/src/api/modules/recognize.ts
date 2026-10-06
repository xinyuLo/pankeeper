/**
 * TMDB 识别 —— 转存弹窗「文件夹更名」一键识别回填。
 * 后端 /api/recognize：复用自识别器（不依赖 QMS/LitePan 刮削记录）。
 * 2026-10-06 起候选式：confident=true 直接回填；歧义时 candidates 给候选卡片让用户挑
 * （实锤：搜"狂飙"想存 F1：狂飙飞车，旧逻辑永远给 2023 剧集）。
 */
import { post } from '../http'

/** 候选条目（海报卡片用） */
export interface RecognizeCandidate {
  tmdb_id: number
  media_type: 'movie' | 'tv'
  title: string
  original_title: string
  year: number | null
  poster: string | null
  overview: string
}

export interface RecognizeResult {
  ok: boolean
  message?: string
  /** true = 算法有把握，media_name 直接回填；false = 有歧义，弹 candidates 让用户挑 */
  confident?: boolean
  title?: string
  media_name?: string
  year?: number | null
  tmdb_id?: number | null
  doubt?: boolean
  candidates?: RecognizeCandidate[]
  /** 后端是否用上了分享清单缓存里的文件名做消歧（仅提示用） */
  used_files?: boolean
}

export interface RecognizeShareRef {
  share_type: string
  share_url: string
  share_code: string
}

/** 识别资源名 → TMDB 候选。confident=true 时 media_name 可直接回填「文件夹更名」。 */
export function recognizeShare(name: string, hint = '', share?: Partial<RecognizeShareRef>): Promise<RecognizeResult> {
  return post<RecognizeResult>('/recognize', {
    name,
    hint,
    share_type: share?.share_type || '',
    share_url: share?.share_url || '',
    share_code: share?.share_code || '',
  })
}
