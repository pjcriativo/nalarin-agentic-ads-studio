import { useEffect, useRef } from 'react';

/**
 * Calls `fn` every `intervalMs` while `enabled`, never overlapping two calls and pausing while the tab is hidden
 * (it catches up as soon as the tab is visible again). Stops for good on unmount / when `enabled` turns false, so a
 * screen that was closed (or an organization that was switched away from) never keeps polling or updating.
 * `fn` may be a new function on every render: the latest one is used.
 */
export default function usePolling(fn, intervalMs, enabled = true) {
    const latest = useRef(fn);
    useEffect(() => { latest.current = fn; });

    useEffect(() => {
        if (!enabled) return undefined;
        let stopped = false;
        let timer = null;
        const schedule = () => {
            if (stopped) return;
            timer = setTimeout(run, intervalMs);
        };
        const run = async () => {
            if (stopped) return;
            if (typeof document !== 'undefined' && document.hidden) { schedule(); return; }
            try { await latest.current(); } catch { /* the caller handles its own errors; polling just keeps its rhythm */ }
            schedule();
        };
        schedule();
        const onVisible = () => {
            if (stopped || document.hidden) return;
            clearTimeout(timer);
            run();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            stopped = true;
            clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [intervalMs, enabled]);
}
