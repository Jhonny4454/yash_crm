import { Link } from "react-router-dom";

export function NotFoundPage() {
  return <main className="system-page"><span>404</span><h1>Page not found</h1><p>The page you requested does not exist or may have moved.</p><Link className="btn primary" to="/">Go to dashboard</Link></main>;
}

export function ForbiddenPage() {
  return <main className="system-page"><span>403</span><h1>Access restricted</h1><p>Your account does not have access to this page.</p><Link className="btn primary" to="/">Return to dashboard</Link></main>;
}
