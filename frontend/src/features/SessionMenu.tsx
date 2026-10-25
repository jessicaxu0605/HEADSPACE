import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  BlurredContainer,
  FadeTransition,
  TextButton,
} from "../component-library";

type SessionMenuProps = {
  onLogout: () => void;
  // Omitted on the workspace selection page, which already lists them all.
  onShowWorkspaces?: () => void;
  // Page actions shown as text buttons beside the toggle.
  actions?: ReactNode;
};

// The fixed top-right menu for a signed-in screen. Closes on an outside
// click or Escape; Escape returns focus to the toggle.
export default function SessionMenu({
  onLogout,
  onShowWorkspaces,
  actions,
}: SessionMenuProps) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent): void => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const choose = (action: () => void): void => {
    setOpen(false);
    action();
  };

  return (
    <div className="session-menu" ref={rootRef}>
      <div className="session-menu-bar">
        {actions}
        <button
          ref={toggleRef}
          className="session-menu-toggle"
          type="button"
          aria-label="Menu"
          aria-expanded={open}
          aria-controls={menuId}
          onClick={() => setOpen((current) => !current)}
        >
          <span aria-hidden="true" />
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </button>
      </div>
      {/* Kept mounted so opening has a hidden frame to fade in from. */}
      <FadeTransition id={menuId} unmountOnExit={false} visible={open}>
        <BlurredContainer
          className="session-menu-panel"
          blurredEdges={["top", "right", "bottom", "left"]}
          edgeBlurSize={12}
        >
          {onShowWorkspaces && (
            <TextButton size="s" onClick={() => choose(onShowWorkspaces)}>
              all spaces
            </TextButton>
          )}
          <TextButton size="s" onClick={() => choose(onLogout)}>
            log out
          </TextButton>
        </BlurredContainer>
      </FadeTransition>
    </div>
  );
}
