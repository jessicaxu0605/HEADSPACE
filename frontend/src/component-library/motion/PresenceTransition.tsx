import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
  type TransitionEvent,
} from "react";
import {
  motionDistances,
  motionDurations,
  motionEasings,
} from "./tokens";

export type PresenceMotion = "fade" | "fade-up";
export type PresenceElement =
  | "article"
  | "div"
  | "li"
  | "p"
  | "section";

export type PresenceTransitionProps = Omit<
  HTMLAttributes<HTMLElement>,
  "children"
> & {
  as?: PresenceElement;
  appear?: boolean;
  children: ReactNode;
  distance?: number | string;
  duration?: number;
  easing?: string;
  enter?: PresenceMotion;
  enterDuration?: number;
  enterEasing?: string;
  enterDelay?: number;
  exit?: PresenceMotion;
  exitDuration?: number;
  exitEasing?: string;
  exitDelay?: number;
  onExited?: () => void;
  unmountOnExit?: boolean;
  visible: boolean;
};

type TransitionPhase = "entering" | "entered" | "exiting";

type PresenceTransitionStyle = CSSProperties & {
  "--presence-distance": string;
  "--presence-fade-easing": string;
  "--presence-enter-duration": string;
  "--presence-enter-easing": string;
  "--presence-enter-delay": string;
  "--presence-exit-duration": string;
  "--presence-exit-easing": string;
  "--presence-exit-delay": string;
};

const distanceValue = (distance: number | string): string =>
  typeof distance === "number" ? `${distance}px` : distance;

export default function PresenceTransition({
  appear = true,
  as: Element = "div",
  children,
  className = "",
  distance = motionDistances.standard,
  duration,
  easing,
  enter = "fade",
  enterDuration = duration ?? motionDurations.standard,
  enterEasing = easing ?? motionEasings.enter,
  enterDelay = 0,
  exit = "fade",
  exitDuration = duration ?? motionDurations.standard,
  exitEasing = easing ?? motionEasings.exit,
  exitDelay = 0,
  onExited,
  onTransitionEnd,
  style,
  unmountOnExit = true,
  visible,
  ...props
}: PresenceTransitionProps) {
  const [isRendered, setIsRendered] = useState(visible || !unmountOnExit);
  const [phase, setPhase] = useState<TransitionPhase>(
    visible && !appear ? "entered" : visible ? "entering" : "exiting",
  );
  const frameRef = useRef<number | undefined>(undefined);
  const exitTimeoutRef = useRef<number | undefined>(undefined);
  const displayedChildrenRef = useRef(children);
  const exitCompletedRef = useRef(visible);
  const onExitedRef = useRef(onExited);
  const previousVisibleRef = useRef(visible);

  onExitedRef.current = onExited;
  if (visible) displayedChildrenRef.current = children;

  useEffect(() => {
    window.cancelAnimationFrame(frameRef.current ?? 0);
    window.clearTimeout(exitTimeoutRef.current);

    if (visible) {
      const shouldAnimateEntry = appear || !previousVisibleRef.current;
      previousVisibleRef.current = true;
      exitCompletedRef.current = false;
      setIsRendered(true);
      if (!shouldAnimateEntry) {
        setPhase("entered");
        return;
      }
      setPhase("entering");
      frameRef.current = window.requestAnimationFrame(() => setPhase("entered"));
      return;
    }

    previousVisibleRef.current = false;
    exitCompletedRef.current = false;
    setPhase("exiting");
    exitTimeoutRef.current = window.setTimeout(() => {
      if (exitCompletedRef.current) return;
      exitCompletedRef.current = true;
      if (unmountOnExit) setIsRendered(false);
      onExitedRef.current?.();
    }, Math.max(0, exitDelay) + Math.max(0, exitDuration) + 50);
  }, [appear, exitDelay, exitDuration, unmountOnExit, visible]);

  useEffect(
    () => () => {
      window.cancelAnimationFrame(frameRef.current ?? 0);
      window.clearTimeout(exitTimeoutRef.current);
    },
    [],
  );

  if (!isRendered) return null;

  const transitionStyle: PresenceTransitionStyle = {
    ...style,
    "--presence-distance": distanceValue(distance),
    "--presence-fade-easing": motionEasings.fade,
    "--presence-enter-duration": `${Math.max(0, enterDuration)}ms`,
    "--presence-enter-easing": enterEasing,
    "--presence-enter-delay": `${Math.max(0, enterDelay)}ms`,
    "--presence-exit-duration": `${Math.max(0, exitDuration)}ms`,
    "--presence-exit-easing": exitEasing,
    "--presence-exit-delay": `${Math.max(0, exitDelay)}ms`,
  };

  const handleTransitionEnd = (
    event: TransitionEvent<HTMLElement>,
  ): void => {
    onTransitionEnd?.(event);
    if (
      event.target === event.currentTarget &&
      event.propertyName === "opacity" &&
      !visible &&
      !exitCompletedRef.current
    ) {
      window.clearTimeout(exitTimeoutRef.current);
      exitCompletedRef.current = true;
      if (unmountOnExit) setIsRendered(false);
      onExitedRef.current?.();
    }
  };

  return (
    <Element
      {...props}
      aria-hidden={!visible || undefined}
      className={`presence-transition is-${phase} enter-${enter} exit-${exit}${
        className ? ` ${className}` : ""
      }`}
      inert={!visible}
      onTransitionEnd={handleTransitionEnd}
      style={transitionStyle}
    >
      {displayedChildrenRef.current}
    </Element>
  );
}
