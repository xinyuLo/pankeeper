import { post } from '../http';
export interface RecognizeCandidate {
    tmdb_id: number;
    media_type: 'movie' | 'tv';
    title: string;
    original_title: string;
    year: number | null;
    poster: string | null;
    overview: string;
}
export interface RecognizeResult {
    ok: boolean;
    message?: string;
    confident?: boolean;
    title?: string;
    media_name?: string;
    year?: number | null;
    tmdb_id?: number | null;
    doubt?: boolean;
    candidates?: RecognizeCandidate[];
    used_files?: boolean;
}
export interface RecognizeShareRef {
    share_type: string;
    share_url: string;
    share_code: string;
}
export function recognizeShare(name: string, hint = '', share?: Partial<RecognizeShareRef>): Promise<RecognizeResult> {
    return post<RecognizeResult>('/recognize', {
        name,
        hint,
        share_type: share?.share_type || '',
        share_url: share?.share_url || '',
        share_code: share?.share_code || '',
    });
}
