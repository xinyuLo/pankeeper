import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue';
export const MOBILE_MAX = 767;
export function useIsMobile(): Ref<boolean> {
    const isMobile = ref(false);
    let mq: MediaQueryList | null = null;
    const update = () => {
        isMobile.value = !!mq?.matches;
    };
    onMounted(() => {
        mq = window.matchMedia(`(max-width: ${MOBILE_MAX}px)`);
        update();
        mq.addEventListener('change', update);
    });
    onBeforeUnmount(() => mq?.removeEventListener('change', update));
    return isMobile;
}
