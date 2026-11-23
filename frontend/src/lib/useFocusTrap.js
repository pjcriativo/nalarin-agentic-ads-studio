import { useEffect } from 'react';

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Keeps keyboard focus inside a dialog while it is open (Tab / Shift+Tab wrap around)
 * and gives focus back to the element that opened it when it closes.
 */
export default function useFocusTrap(containerRef, active) {
    useEffect(() => {
        if (!active) return undefined;
        const opener = document.activeElement;
        const onKeyDown = (event) => {
            if (event.key !== 'Tab' || !containerRef.current) return;
            const items = [...containerRef.current.querySelectorAll(FOCUSABLE)].filter((el) => getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden');
            if (items.length === 0) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (event.shiftKey && (document.activeElement === first || !containerRef.current.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !containerRef.current.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', onKeyDown);
        return () => {
            document.removeEventListener('keydown', onKeyDown);
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
        };
    }, [containerRef, active]);
}
