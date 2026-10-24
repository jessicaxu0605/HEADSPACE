import FadeTransition from "./motion/FadeTransition";
import { motionDurations } from "./motion/tokens";

type BrainLoaderProps = {
  fading?: boolean;
  label: string;
};

export default function BrainLoader({
  fading = false,
  label,
}: BrainLoaderProps) {
  return (
    <FadeTransition
      className="brain-loader"
      duration={motionDurations.loader}
      visible={!fading}
      role="status"
      aria-live="polite"
    >
      <img src="/brain_loader.svg" alt="" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </FadeTransition>
  );
}
