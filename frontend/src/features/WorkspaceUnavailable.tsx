import { TextButton } from "../component-library";

type WorkspaceUnavailableProps = {
  // Shown when the workspace list could not be loaded; omitted when the
  // account simply has no active memberships.
  error?: string;
  onRetry?: () => void;
  onLogout: () => void;
};

export default function WorkspaceUnavailable({
  error,
  onRetry,
  onLogout,
}: WorkspaceUnavailableProps) {
  return (
    <main className="auth-shell">
      <section
        className="auth-card auth-welcome workspace-unavailable"
        aria-labelledby="workspace-unavailable-title"
      >
        <h1 id="workspace-unavailable-title">
          {error ? "Couldn’t open your headspace" : "No headspace yet"}
        </h1>
        <p className="workspace-unavailable-message" role={error ? "alert" : undefined}>
          {error ?? "This account isn’t a member of any space."}
        </p>
        <div className="auth-entry-actions">
          {onRetry && (
            <TextButton className="auth-entry-action" onClick={onRetry}>
              try again →
            </TextButton>
          )}
          <TextButton className="auth-entry-action" onClick={onLogout}>
            log out
          </TextButton>
        </div>
      </section>
    </main>
  );
}
