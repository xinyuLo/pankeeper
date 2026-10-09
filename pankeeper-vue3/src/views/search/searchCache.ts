import type { DriveType, SearchResultItem } from '@/types/model';
let savedKw = '';
let savedResults: SearchResultItem[] | null = null;
let savedActive: 'all' | DriveType = 'all';
let savedElapsed: string | null = null;
export function saveSearchCache(s: {
    kw: string;
    results: SearchResultItem[];
    active: 'all' | DriveType;
    elapsed: string | null;
}) {
    savedKw = s.kw;
    savedResults = s.results;
    savedActive = s.active;
    savedElapsed = s.elapsed;
}
export function loadSearchCache() {
    return savedResults
        ? { kw: savedKw, results: savedResults, active: savedActive, elapsed: savedElapsed }
        : null;
}
