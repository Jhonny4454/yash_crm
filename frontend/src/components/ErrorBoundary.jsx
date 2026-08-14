import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("React rendering error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <main className="fatal-error">
      <h1>We could not open this page</h1>
      <p>The error was contained so you can safely return to the dashboard.</p>
      <button className="btn primary" onClick={() => {
        this.setState({ error: null });
        window.location.assign("/app/");
      }}>Return to dashboard</button>
    </main>;
  }
}
