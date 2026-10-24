import { FadeTransition } from "../component-library";
import { BRAIN_LOADER_FADE_MS } from "../loading";

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
      duration={BRAIN_LOADER_FADE_MS}
      visible={!fading}
      role="status"
      aria-live="polite"
    >
      <img src="/brain_loader.svg" alt="" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </FadeTransition>
  );
}
