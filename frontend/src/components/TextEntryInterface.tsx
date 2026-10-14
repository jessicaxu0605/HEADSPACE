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

type TextEntryInterfaceProps = {
  sectionRef: RefObject<HTMLElement | null>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
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
}: TextEntryInterfaceProps) {
  const [content, setContent] = useState("");
  const [status, setStatus] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showSubmit, setShowSubmit] = useState(false);
  const [scrollFades, setScrollFades] = useState({ up: false, down: false });
  const retryRequestRef = useRef<{ content: string; id: string } | null>(null);
  const revealButtonTimeoutRef = useRef<number | undefined>(undefined);
  const autoScrollFrameRef = useRef<number | undefined>(undefined);
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

  useEffect(
    () => () => {
      window.clearTimeout(revealButtonTimeoutRef.current);
      if (autoScrollFrameRef.current !== undefined) {
        window.cancelAnimationFrame(autoScrollFrameRef.current);
      }
    },
    [],
  );

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const nextContent = event.target.value;
    const wasScrolledUp = scrollFades.down;
    setContent(nextContent);
    setStatus("");
    if (!wasScrolledUp) {
      keepBottomFadeHiddenRef.current = true;
      window.requestAnimationFrame(scrollToBottomSoftly);
    }
    if (
      retryRequestRef.current &&
      retryRequestRef.current.content !== nextContent
    ) {
      retryRequestRef.current = null;
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!content.trim()) {
      textareaRef.current?.focus();
      return;
    }

    if (!retryRequestRef.current || retryRequestRef.current.content !== content) {
      retryRequestRef.current = { content, id: newRequestId() };
    }
    const request = retryRequestRef.current;
    setSubmitting(true);
    setStatus("");

    try {
      await createManualIngestion(content, request.id);
      setContent("");
      setShowSubmit(false);
      setScrollFades({ up: false, down: false });
      retryRequestRef.current = null;
      window.clearTimeout(revealButtonTimeoutRef.current);
      revealButtonTimeoutRef.current = undefined;
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Something went wrong.";
      setStatus(`Couldn’t remember this thought. ${message} Please try again.`);
    } finally {
      setSubmitting(false);
    }
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
            disabled={submitting}
            onChange={handleChange}
            onScroll={updateScrollFades}
            onTransitionEnd={updateScrollFades}
            onWheel={releaseAutoScroll}
            onTouchStart={releaseAutoScroll}
          />
        </div>
        <button
          id="submit-button"
          className={`text-button${showSubmit ? " is-visible" : ""}${submitting ? " is-loading" : ""}`}
          type="submit"
          disabled={submitting}
          aria-hidden={!showSubmit}
          aria-busy={submitting}
          tabIndex={showSubmit ? 0 : -1}
        >
          remember this&nbsp; →
        </button>
      </form>
      {status && (
        <p className="status" role="alert">
          {status}
        </p>
      )}
    </section>
  );
}
