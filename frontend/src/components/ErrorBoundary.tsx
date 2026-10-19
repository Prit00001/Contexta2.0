import { Component, type ReactNode } from "react";

/** Keeps one broken view from blanking the whole app. Navigating away (a new `resetKey`) clears the
 *  error; healthy views are not remounted on navigation. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  componentDidCatch(error: Error) {
    console.error("[contexta] view crashed", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="center" role="alert">
        <h1>Something went wrong</h1>
        <p className="muted">This page hit an unexpected error. Reload to try again.</p>
        <button className="btn" onClick={() => location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
