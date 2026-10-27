import { Component, type ErrorInfo, type ReactNode } from 'react';
import { logger } from '../../utils/logger';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Rendered instead of the children after an error. */
  fallback(reset: () => void): ReactNode;
  /** When this value changes (e.g. the route), the boundary resets. */
  resetKey?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  resetKey?: string;
}

/**
 * Stops a rendering error in one page from taking down the whole app. The
 * error is logged without its message or stack, which could contain data.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { hasError: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  static getDerivedStateFromProps(props: ErrorBoundaryProps, state: ErrorBoundaryState): Partial<ErrorBoundaryState> | null {
    if (props.resetKey !== state.resetKey) return { hasError: false, resetKey: props.resetKey };
    return null;
  }

  override componentDidCatch(error: Error, _info: ErrorInfo): void {
    logger.error('Unhandled rendering error', { name: error.name });
  }

  reset = () => this.setState({ hasError: false });

  override render() {
    return this.state.hasError ? this.props.fallback(this.reset) : this.props.children;
  }
}
