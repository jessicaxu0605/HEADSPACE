import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type RefObject,
} from "react";

type AutoGrowingTextAreaProps = {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onValueChange: (value: string) => void;
  ariaLabel: string;
  emptyPrompt?: string;
  id?: string;
  name?: string;
  maxLength?: number;
  required?: boolean;
  autoFocus?: boolean;
  className?: string;
};

const textareaMaxHeight = 240;

export default function AutoGrowingTextArea({
  textareaRef,
  value,
  onValueChange,
  ariaLabel,
  emptyPrompt,
  id,
  name,
  maxLength,
  required,
  autoFocus,
  className = "",
}: AutoGrowingTextAreaProps) {
  const [scrollFades, setScrollFades] = useState({ up: false, down: false });
  const autoScrollFrameRef = useRef<number | undefined>(undefined);
  const keepBottomFadeHiddenRef = useRef(false);
  const hasText = Boolean(value.trim());

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

    if (!value) textarea.scrollTop = 0;
    const currentHeight = textarea.getBoundingClientRect().height;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, textareaMaxHeight);
    textarea.style.height = `${currentHeight}px`;
    void textarea.offsetHeight;
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > textareaMaxHeight ? "auto" : "hidden";
    updateScrollFades();
  }, [textareaRef, updateScrollFades, value]);

  useEffect(
    () => () => {
      if (autoScrollFrameRef.current !== undefined) {
        window.cancelAnimationFrame(autoScrollFrameRef.current);
      }
    },
    [],
  );

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const wasScrolledUp = scrollFades.down;
    onValueChange(event.target.value);
    if (!wasScrolledUp) {
      keepBottomFadeHiddenRef.current = true;
      window.requestAnimationFrame(scrollToBottomSoftly);
    }
  };

  const classes = [
    "text-entry",
    className,
    hasText && "has-text",
    scrollFades.up && "can-scroll-up",
    scrollFades.down && "can-scroll-down",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      {emptyPrompt && (
        <span className="empty-prompt" aria-hidden="true">
          {emptyPrompt}
        </span>
      )}
      <textarea
        ref={textareaRef}
        id={id}
        name={name}
        rows={1}
        aria-label={ariaLabel}
        maxLength={maxLength}
        required={required}
        autoFocus={autoFocus}
        value={value}
        onChange={handleChange}
        onScroll={updateScrollFades}
        onTransitionEnd={updateScrollFades}
        onWheel={releaseAutoScroll}
        onTouchStart={releaseAutoScroll}
      />
    </div>
  );
}
