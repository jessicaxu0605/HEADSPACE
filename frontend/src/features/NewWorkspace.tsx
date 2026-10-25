import { useState, type FormEvent } from "react";
import { Checkbox, TextButton, ValidatedTextField } from "../component-library";
import SessionMenu from "./SessionMenu";

type NewWorkspaceProps = {
  onCreate: (name: string, createSelfNode: boolean) => Promise<void>;
  onBack: () => void;
  onLogout: () => void;
};

// A blank name only keeps "create" disabled; the field's warning is reserved
// for server errors.
const noLocalWarning = (): string => "";

// Names a new workspace. Creating it opens it, so this screen leaves with the
// selection page on success.
export default function NewWorkspace({ onCreate, onBack, onLogout }: NewWorkspaceProps) {
  const [name, setName] = useState("");
  const [createSelfNode, setCreateSelfNode] = useState(true);
  const [creating, setCreating] = useState(false);
  const [serverWarning, setServerWarning] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (creating || !name.trim()) return;
    setCreating(true);
    setServerWarning("");
    try {
      await onCreate(name.trim(), createSelfNode);
    } catch (error) {
      setServerWarning(
        error instanceof Error ? error.message : "Something went wrong.",
      );
      setCreating(false);
    }
  };

  return (
    <main className="auth-shell">
      <SessionMenu onLogout={onLogout} onShowWorkspaces={onBack} />
      <section className="auth-card auth-signup" aria-labelledby="new-workspace-title">
        <h1 id="new-workspace-title">A new headspace</h1>
        <form
          className="auth-signup-form"
          noValidate
          onSubmit={(event) => void submit(event)}
        >
          <div className="auth-signup-fields">
            <ValidatedTextField
              aria-label="Space name"
              placeholder="name"
              maxLength={200}
              value={name}
              onValueChange={(nextName) => {
                setName(nextName);
                setServerWarning("");
              }}
              validate={noLocalWarning}
              externalWarning={serverWarning}
              disabled={creating}
              required
              autoFocus
            />
            {/* Adds the user's profile to the new workspace as a person node. */}
            <Checkbox
              checked={createSelfNode}
              onCheckedChange={setCreateSelfNode}
              disabled={creating}
            >
              add me to this space
            </Checkbox>
          </div>
          <div className="auth-signup-actions">
            <TextButton
              className="auth-entry-action"
              onClick={onBack}
              disabled={creating}
            >
              ← back
            </TextButton>
            <TextButton
              className="auth-entry-action"
              type="submit"
              disabled={creating || !name.trim()}
            >
              {creating ? "creating…" : "create →"}
            </TextButton>
          </div>
        </form>
      </section>
    </main>
  );
}
