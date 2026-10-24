import type { Key } from "react";
import FadeSwitch, { type FadeSwitchProps } from "./FadeSwitch";
import { motionDurations } from "./tokens";

export type PageTransitionProps = Omit<
  FadeSwitchProps,
  "duration" | "transitionKey"
> & {
  duration?: number;
  pageKey: Key;
};

export default function PageTransition({
  duration = motionDurations.page,
  pageKey,
  ...props
}: PageTransitionProps) {
  return (
    <FadeSwitch {...props} duration={duration} transitionKey={pageKey} />
  );
}
