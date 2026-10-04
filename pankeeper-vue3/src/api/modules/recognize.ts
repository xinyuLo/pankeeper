/**
 * TMDB 识别 —— 转存弹窗「文件夹更名」一键识别回填。
 * 后端 /api/recognize：复用自识别器（不依赖 QMS/LitePan 刮削记录）。
 */
import { post } from '../http'

export interface RecognizeResult {
  ok: boolean
  message?: string
  title?: string
  media_name?: string
  year?: number | null
  tmdb_id?: number | null
  doubt?: boolean
}

/** 识别资源名 → TMDB。识别成功时 media_name 可直接回填「文件夹更名」。 */
export function recognizeShare(name: string, hint = ''): Promise<RecognizeResult> {
  return post<RecognizeResult>('/recognize', { name, hint })
}
