import { Component, ReactNode } from "react";

/** Une page qui plante (donnée inattendue d'un NVR…) affiche un message au lieu d'une page blanche. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card" role="alert" style={{ margin: 16 }}>
        <h2>Cette page n’a pas pu s’afficher</h2>
        <p className="muted">La surveillance continue. Rechargez la page ; si le problème revient, notez ce message pour
          l’installateur : <code>{this.state.error.message}</code></p>
        <button className="btn primary" onClick={() => location.reload()}>Recharger la page</button>
      </div>
    );
  }
}
