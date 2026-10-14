import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type RefObject,
} from "react";
import { createManualIngestion } from "../api";
import BlurredContainer from "./BlurredContainer";

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

const textareaMaxHeight = 240;

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

const newRequestId = (): string => {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export default function TextEntryInterface({
  sectionRef,
  textareaRef,
  onViewRun,
}: TextEntryInterfaceProps) {
  const [content, setContent] = useState("");
  const [notifications, setNotifications] = useState<
    SubmissionNotification[]
  >([]);
  const [showSubmit, setShowSubmit] = useState(false);
  const [scrollFades, setScrollFades] = useState({ up: false, down: false });
  const revealButtonTimeoutRef = useRef<number | undefined>(undefined);
  const autoScrollFrameRef = useRef<number | undefined>(undefined);
  const notificationTimeoutsRef = useRef(new Map<string, number>());
  const isMountedRef = useRef(true);
  const keepBottomFadeHiddenRef = useRef(false);
  const hasText = Boolean(content.trim());

  const updateScrollFades = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const threshold = 2;
    const canScroll = textarea.scrollHeight > textareaMaxHeight + threshold;
    const isAtBottom =
      textarea.scrollTop + textarea.clientHeight >=
      textarea.scrollHeight - threshold;
    setScrollFades({
      up: canScroll && textarea.scrollTop > threshold,
      down: canScroll && !isAtBottom && !keepBottomFadeHiddenRef.current,
    });
  }, [textareaRef]);

  const releaseAutoScroll = useCallback(() => {
    if (autoScrollFrameRef.current !== undefined) {
      window.cancelAnimationFrame(autoScrollFrameRef.current);
      autoScrollFrameRef.current = undefined;
    }
    keepBottomFadeHiddenRef.current = false;
    updateScrollFades();
  }, [updateScrollFades]);

  const scrollToBottomSoftly = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    if (autoScrollFrameRef.current !== undefined) {
      window.cancelAnimationFrame(autoScrollFrameRef.current);
    }

    const start = textarea.scrollTop;
    const target = Math.max(0, textarea.scrollHeight - textarea.clientHeight);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      textarea.scrollTop = target;
      releaseAutoScroll();
      return;
    }

    const duration = 850;
    const startedAt = performance.now();
    const step = (now: number): void => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const easedProgress = 1 - Math.pow(1 - progress, 3);
      textarea.scrollTop = start + (target - start) * easedProgress;
      if (progress < 1) {
        autoScrollFrameRef.current = window.requestAnimationFrame(step);
      } else {
        autoScrollFrameRef.current = undefined;
        keepBottomFadeHiddenRef.current = false;
        updateScrollFades();
      }
    };
    autoScrollFrameRef.current = window.requestAnimationFrame(step);
  }, [releaseAutoScroll, textareaRef, updateScrollFades]);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const currentHeight = textarea.getBoundingClientRect().height;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, textareaMaxHeight);
    textarea.style.height = `${currentHeight}px`;
    void textarea.offsetHeight;
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > textareaMaxHeight ? "auto" : "hidden";
    updateScrollFades();
  }, [content, textareaRef, updateScrollFades]);

  useEffect(() => {
    if (!hasText) {
      window.clearTimeout(revealButtonTimeoutRef.current);
      revealButtonTimeoutRef.current = undefined;
      setShowSubmit(false);
      return;
    }
    if (!showSubmit && revealButtonTimeoutRef.current === undefined) {
      revealButtonTimeoutRef.current = window.setTimeout(() => {
        revealButtonTimeoutRef.current = undefined;
        setShowSubmit(true);
      }, 500);
    }
  }, [hasText, showSubmit]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      window.clearTimeout(revealButtonTimeoutRef.current);
      if (autoScrollFrameRef.current !== undefined) {
        window.cancelAnimationFrame(autoScrollFrameRef.current);
      }
      notificationTimeoutsRef.current.forEach((timeout) =>
        window.clearTimeout(timeout),
      );
    };
  }, []);

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const nextContent = event.target.value;
    const wasScrolledUp = scrollFades.down;
    setContent(nextContent);
    if (!wasScrolledUp) {
      keepBottomFadeHiddenRef.current = true;
      window.requestAnimationFrame(scrollToBottomSoftly);
    }
  };

  const dismissNotification = useCallback((id: string): void => {
    const timeout = notificationTimeoutsRef.current.get(id);
    if (timeout !== undefined) window.clearTimeout(timeout);
    notificationTimeoutsRef.current.delete(id);
    setNotifications((current) =>
      current.filter((notification) => notification.id !== id),
    );
  }, []);

  const submitInBackground = useCallback(
    async (request: SubmissionNotification) => {
      try {
        const run = await createManualIngestion(
          request.content,
          request.requestId,
        );
        if (!isMountedRef.current) return;
        setNotifications((current) =>
          current.map((notification) =>
            notification.id === request.id
              ? {
                  ...notification,
                  state: "success",
                  message: undefined,
                  runId: run.id,
                }
              : notification,
          ),
        );
        const timeout = window.setTimeout(
          () => dismissNotification(request.id),
          6000,
        );
        notificationTimeoutsRef.current.set(request.id, timeout);
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
    [dismissNotification],
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
    setShowSubmit(false);
    setScrollFades({ up: false, down: false });
    window.clearTimeout(revealButtonTimeoutRef.current);
    revealButtonTimeoutRef.current = undefined;
    window.requestAnimationFrame(() => textareaRef.current?.focus());
    void submitInBackground(request);
  };

  const starterPrompt = useRef(
    starterPrompts[Math.floor(Math.random() * starterPrompts.length)],
  ).current;
  const textEntryClasses = [
    "text-entry",
    hasText && "has-text",
    scrollFades.up && "can-scroll-up",
    scrollFades.down && "can-scroll-down",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      ref={sectionRef}
      id="entry"
      className="entry screen"
      aria-label="Add a thought"
    >
      <form className="ingestion-form" onSubmit={handleSubmit}>
        <div className={textEntryClasses}>
          <span className="empty-prompt" aria-hidden="true">
            {starterPrompt}
          </span>
          <textarea
            ref={textareaRef}
            id="content"
            name="content"
            rows={1}
            aria-label="Your thought"
            required
            value={content}
            onChange={handleChange}
            onScroll={updateScrollFades}
            onTransitionEnd={updateScrollFades}
            onWheel={releaseAutoScroll}
            onTouchStart={releaseAutoScroll}
          />
        </div>
        <button
          id="submit-button"
          className={`text-button${showSubmit ? " is-visible" : ""}`}
          type="submit"
          aria-hidden={!showSubmit}
          tabIndex={showSubmit ? 0 : -1}
        >
          remember this&nbsp; →
        </button>
      </form>
      <div className="notification-stack" aria-live="polite">
        {notifications.map((notification) => (
          <BlurredContainer
            blurredEdges={["top", "right", "bottom", "left"]}
            edgeBlurSize={18}
            className={`notification ${notification.state}`}
            key={notification.id}
            role={notification.state === "error" ? "alert" : "status"}
          >
            <p>
              {notification.state === "pending"
                ? "committing to memory"
                : notification.state === "success"
                  ? "your memory is ready"
                  : notification.message}
            </p>
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
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismissNotification(notification.id)}
              >
                dismiss
              </button>
            </div>
          </BlurredContainer>
        ))}
      </div>
    </section>
  );
}
