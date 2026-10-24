import {
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type Key,
  type ReactNode,
} from "react";
import FadeTransition from "./FadeTransition";
import { motionDurations } from "./tokens";

export type FadeSwitchProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  "children"
> & {
  appear?: boolean;
  children: ReactNode;
  duration?: number;
  easing?: string;
  onSwitched?: (transitionKey: Key) => void;
  transitionKey: Key;
};

export default function FadeSwitch({
  appear = true,
  children,
  duration = motionDurations.standard,
  easing,
  onSwitched,
  transitionKey,
  ...props
}: FadeSwitchProps) {
  const [displayedKey, setDisplayedKey] = useState(transitionKey);
  const [visible, setVisible] = useState(true);
  const displayedChildrenRef = useRef(children);
  const latestRef = useRef({ children, transitionKey });
  const onSwitchedRef = useRef(onSwitched);
  const switchedFrameRef = useRef<number | undefined>(undefined);

  latestRef.current = { children, transitionKey };
  onSwitchedRef.current = onSwitched;
  if (Object.is(displayedKey, transitionKey)) {
    displayedChildrenRef.current = children;
  }

  useEffect(() => {
    setVisible(Object.is(displayedKey, transitionKey));
  }, [displayedKey, transitionKey]);

  useEffect(
    () => () => window.cancelAnimationFrame(switchedFrameRef.current ?? 0),
    [],
  );

  return (
    <FadeTransition
      {...props}
      appear={appear}
      duration={duration}
      easing={easing}
      unmountOnExit={false}
      visible={visible}
      onExited={() => {
        const next = latestRef.current;
        displayedChildrenRef.current = next.children;
        setDisplayedKey(next.transitionKey);
        setVisible(true);
        switchedFrameRef.current = window.requestAnimationFrame(() =>
          onSwitchedRef.current?.(next.transitionKey),
        );
      }}
    >
      {displayedChildrenRef.current}
    </FadeTransition>
  );
}
