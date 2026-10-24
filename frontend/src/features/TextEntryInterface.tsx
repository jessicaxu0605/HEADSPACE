import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from "react";
import { createManualIngestion } from "../api";
import { useWorkspace } from "../providers/WorkspaceProvider";
import {
  AutoGrowingTextArea,
  BlurredContainer,
  PresenceTransition,
  TextButton,
  motionDurations,
} from "../component-library";

type TextEntryInterfaceProps = {
  sectionRef: RefObject<HTMLElement | null>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onViewRun: (runId: string) => void;
};

type SubmissionNotification = {
  id: string;
  content: string;
  requestId: string;
  state: "pending" | "success" | "error";
  message?: string;
  runId?: string;
};

const starterPrompts = [
  "What’s on your mind?",
  "What’s lingering?",
  "Who have you been thinking about?",
  "Penny for your thoughts",
  "What made today different?",
  "Anything worth saving?",
  "What’s the story?",
  "What’s still floating around?",
  "Anything unfinished?",
  "What’s on the horizon?",
  "Start from the beginning",
  "All thoughts welcome here",
  "One thing worth keeping:",
  "How was your day?",
] as const;

// The submit button waits for a pause in typing, like field validation.
const submitRevealDelayMs = 500;

const newRequestId = (): string => {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export default function TextEntryInterface({
  sectionRef,
  textareaRef,
  onViewRun,
}: TextEntryInterfaceProps) {
  const { workspace } = useWorkspace();
  const [content, setContent] = useState("");
  const [notifications, setNotifications] = useState<SubmissionNotification[]>(
    [],
  );
  const [closingNotificationIds, setClosingNotificationIds] = useState<
    Set<string>
  >(new Set());
  const isMountedRef = useRef(true);
  const hasText = Boolean(content.trim());

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const removeNotification = useCallback((id: string): void => {
    setNotifications((current) =>
      current.filter((notification) => notification.id !== id),
    );
    setClosingNotificationIds((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  }, []);

  const dismissNotification = useCallback((id: string): void => {
    setClosingNotificationIds((current) => new Set(current).add(id));
  }, []);

  const submitInBackground = useCallback(
    async (request: SubmissionNotification) => {
      try {
        const run = await createManualIngestion(
          workspace.id,
          request.content,
          request.requestId,
        );
        if (!isMountedRef.current) return;
        // The pending notice goes away and a separate ready notice takes its
        // place; the ready notice stays until the reader dismisses it.
        setNotifications((current) => [
          ...current.filter((notification) => notification.id !== request.id),
          {
            ...request,
            id: `${request.id}:ready`,
            state: "success",
            message: undefined,
            runId: run.id,
          },
        ]);
      } catch (error) {
        if (!isMountedRef.current) return;
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setNotifications((current) =>
          current.map((notification) =>
            notification.id === request.id
              ? { ...notification, state: "error", message }
              : notification,
          ),
        );
      }
    },
    [workspace.id],
  );

  const retrySubmission = (notification: SubmissionNotification): void => {
    setNotifications((current) =>
      current.map((item) =>
        item.id === notification.id
          ? { ...item, state: "pending", message: undefined }
          : item,
      ),
    );
    void submitInBackground(notification);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!content.trim()) {
      textareaRef.current?.focus();
      return;
    }

    const requestId = newRequestId();
    const request: SubmissionNotification = {
      id: requestId,
      content,
      requestId,
      state: "pending",
    };
    setNotifications((current) => [...current, request]);
    setContent("");
    window.requestAnimationFrame(() => textareaRef.current?.focus());
    void submitInBackground(request);
  };

  const starterPrompt = useRef(
    starterPrompts[Math.floor(Math.random() * starterPrompts.length)],
  ).current;
  return (
    <section
      ref={sectionRef}
      id="entry"
      className="entry screen"
      aria-label="Add a thought"
    >
      <PresenceTransition
        enter="fade-up"
        exit="fade-up"
        visible
      >
        <form className="ingestion-form" onSubmit={handleSubmit}>
          <AutoGrowingTextArea
            textareaRef={textareaRef}
            value={content}
            onValueChange={setContent}
            ariaLabel="Your thought"
            emptyPrompt={starterPrompt}
            id="content"
            name="content"
            required
          />
          <PresenceTransition
            enter="fade-up"
            enterDelay={submitRevealDelayMs}
            enterDuration={motionDurations.page}
            exit="fade-up"
            exitDuration={motionDurations.standard}
            unmountOnExit={false}
            visible={hasText}
          >
            <TextButton className="ingestion-submit" type="submit">
              remember this&nbsp; →
            </TextButton>
          </PresenceTransition>
        </form>
      </PresenceTransition>
      <div className="notification-stack" aria-live="polite">
        {notifications.map((notification) => (
          <PresenceTransition
            enter="fade-up"
            enterDuration={motionDurations.page}
            exit="fade-up"
            exitDuration={motionDurations.standard}
            key={notification.id}
            onExited={() => removeNotification(notification.id)}
            visible={!closingNotificationIds.has(notification.id)}
          >
            <BlurredContainer
              blurredEdges={["top", "right", "bottom", "left"]}
              edgeBlurSize={18}
              className={`notification ${notification.state}`}
              role={notification.state === "error" ? "alert" : "status"}
            >
              <button
                className="notification-dismiss"
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismissNotification(notification.id)}
              >
                <span aria-hidden="true">×</span>
              </button>
              <p>
                {notification.state === "pending"
                  ? "committing to memory..."
                  : notification.state === "success"
                    ? "your memory is ready!"
                    : notification.message}
              </p>
              {(notification.state === "error" ||
                (notification.state === "success" && notification.runId)) && (
                <div className="notification-actions">
                  {notification.state === "success" && notification.runId && (
                    <button
                      type="button"
                      onClick={() => {
                        const runId = notification.runId;
                        dismissNotification(notification.id);
                        if (runId) onViewRun(runId);
                      }}
                    >
                      view
                    </button>
                  )}
                  {notification.state === "error" && (
                    <button
                      type="button"
                      onClick={() => retrySubmission(notification)}
                    >
                      retry
                    </button>
                  )}
                </div>
              )}
            </BlurredContainer>
          </PresenceTransition>
        ))}
      </div>
    </section>
  );
}
