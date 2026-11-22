import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';

import i18n from '../i18n';
import OptimizationPanel from './OptimizationPanel';

const authFetch = vi.fn();

vi.mock('../context/AuthContext', () => ({
    useAuth: () => ({ authFetch }),
}));

vi.mock('../context/ToastContext', () => ({
    useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn() }),
}));

const status = (overrides = {}) => ({
    ok: true,
    json: async () => ({
        kill_switch_active: false,
        engine_active: false,
        auto_enabled: true,
        allowed_actions: ['campaign.pause'],
        max_budget_micros: null,
        max_daily_spend_micros: null,
        max_actions_per_day: null,
        operating_hours: { start: null, end: null, timezone: 'America/Sao_Paulo' },
        ...overrides,
    }),
});

afterEach(async () => {
    await act(async () => {
        await i18n.changeLanguage('en-US');
    });
});

describe('OptimizationPanel never presents inactive autonomy as real', () => {
    it('says autonomous optimization is inactive and does not show "Auto-Mode ON"', async () => {
        authFetch.mockResolvedValue(status({ engine_active: false, auto_enabled: true }));
        render(<OptimizationPanel />);

        expect(await screen.findByText(/Autonomous optimization is not active/i)).toBeInTheDocument();
        expect(screen.queryByText('Auto-Mode ON')).not.toBeInTheDocument();
        expect(screen.getByText('Manual Guardrail Only')).toBeInTheDocument();
        expect(screen.getByText(/Not monitored/i)).toBeInTheDocument();
        expect(screen.queryByText('24/7 Monitored')).not.toBeInTheDocument();
    });

    it('shows the operating window with its configured timezone, not a hard-coded WIB', async () => {
        authFetch.mockResolvedValue(
            status({ operating_hours: { start: 8, end: 18, timezone: 'America/Sao_Paulo' } })
        );
        render(<OptimizationPanel />);

        expect(await screen.findByText(/8:00 - 18:00 \(America\/Sao_Paulo\)/)).toBeInTheDocument();
        expect(screen.queryByText(/WIB/)).not.toBeInTheDocument();
    });

    it('only shows Auto-Mode ON once an execution engine is really active', async () => {
        authFetch.mockResolvedValue(status({ engine_active: true, auto_enabled: true }));
        render(<OptimizationPanel />);

        expect(await screen.findByText('Auto-Mode ON')).toBeInTheDocument();
        expect(screen.queryByText(/Autonomous optimization is not active/i)).not.toBeInTheDocument();
    });

    it('is fully translated in pt-BR', async () => {
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        authFetch.mockResolvedValue(status());
        render(<OptimizationPanel />);

        await waitFor(() =>
            expect(screen.getByText(/otimização autônoma não está ativa/i)).toBeInTheDocument()
        );
        expect(screen.queryByText(/Autonomous optimization/i)).not.toBeInTheDocument();
    });
});
