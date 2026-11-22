import React from 'react';
import ErrorFallback from './ErrorFallback';

/**
 * Global render-crash boundary (Sprint 8). Before this, a throw inside any
 * page component unmounted the whole React tree -> blank white screen with
 * no recovery. This boundary renders a small retry card instead and logs the
 * error to the browser console for triage. Data-fetch errors do NOT land here
 * (pages handle those via useToast) — only render-time exceptions do.
 */
export default class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { error: null };
    }

    static getDerivedStateFromError(error) {
        return { error };
    }

    componentDidCatch(error, info) {
        console.error('[ErrorBoundary] render crash:', error, info?.componentStack);
    }

    handleRetry = () => {
        this.setState({ error: null });
    };

    render() {
        if (this.state.error) {
            return <ErrorFallback onRetry={this.handleRetry} />;
        }
        return this.props.children;
    }
}
