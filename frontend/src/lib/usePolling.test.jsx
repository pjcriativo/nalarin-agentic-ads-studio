import React from 'react';
import { act, cleanup, render } from '@testing-library/react';
import usePolling from './usePolling';

function Probe({ fn, ms = 1000, enabled = true }) {
    usePolling(fn, ms, enabled);
    return null;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('usePolling', () => {
    it('calls at the interval, never overlaps two calls, and uses the latest function', async () => {
        let release;
        const slow = vi.fn(() => new Promise((resolve) => { release = resolve; }));
        const { rerender } = render(<Probe fn={slow} />);
        await act(async () => { vi.advanceTimersByTime(1000); });
        expect(slow).toHaveBeenCalledTimes(1);
        await act(async () => { vi.advanceTimersByTime(5000); });
        expect(slow).toHaveBeenCalledTimes(1); // still waiting for the previous call
        const fast = vi.fn(async () => {});
        rerender(<Probe fn={fast} />);
        await act(async () => { release(); });
        await act(async () => { vi.advanceTimersByTime(1000); });
        expect(fast).toHaveBeenCalledTimes(1);
        expect(slow).toHaveBeenCalledTimes(1);
    });

    it('stops on unmount and when disabled, and survives an error', async () => {
        const fn = vi.fn(async () => { throw new Error('boom'); });
        const { rerender, unmount } = render(<Probe fn={fn} />);
        for (let i = 0; i < 3; i += 1) await act(async () => { vi.advanceTimersByTime(1000); });
        expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2); // kept its rhythm after an error
        rerender(<Probe fn={fn} enabled={false} />);
        const count = fn.mock.calls.length;
        await act(async () => { vi.advanceTimersByTime(5000); });
        expect(fn).toHaveBeenCalledTimes(count);
        rerender(<Probe fn={fn} />);
        unmount();
        await act(async () => { vi.advanceTimersByTime(5000); });
        expect(fn).toHaveBeenCalledTimes(count);
    });

    it('pauses while the tab is hidden and catches up when it is visible again', async () => {
        const fn = vi.fn(async () => {});
        render(<Probe fn={fn} />);
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
        await act(async () => { vi.advanceTimersByTime(4000); });
        expect(fn).not.toHaveBeenCalled();
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
        await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
        expect(fn).toHaveBeenCalledTimes(1);
    });
});
