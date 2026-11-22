import React from 'react';
import { render, screen, act } from '@testing-library/react';

import i18n from '../i18n';
import Reporting from './Reporting';

afterEach(async () => {
    await act(async () => {
        await i18n.changeLanguage('en-US');
    });
});

describe('Reporting (sample data)', () => {
    it('labels the figures as sample data instead of implying they are real', () => {
        render(<Reporting />);
        expect(screen.getByRole('status')).toHaveTextContent(/Sample data for layout preview only/i);
    });

    it('renders the same bars on every render (no Math.random in render)', () => {
        const spy = vi.spyOn(Math, 'random');
        const { rerender } = render(<Reporting />);
        rerender(<Reporting />);
        expect(spy).not.toHaveBeenCalled();
        spy.mockRestore();
    });

    it('shows the notice in pt-BR', async () => {
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        render(<Reporting />);
        expect(screen.getByRole('status')).toHaveTextContent(/Dados de exemplo/i);
    });
});
