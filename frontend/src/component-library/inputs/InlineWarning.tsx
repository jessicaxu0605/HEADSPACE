import FadeTransition from "../motion/FadeTransition";
import { motionDurations } from "../motion/tokens";

type InlineWarningProps = {
  fadeDuration?: number;
  message?: string;
};

export default function InlineWarning({
  fadeDuration = motionDurations.standard,
  message,
}: InlineWarningProps) {
  return (
    <FadeTransition duration={fadeDuration} visible={Boolean(message)}>
      <p className="field-warning" role="alert">
        {message}
      </p>
    </FadeTransition>
  );
}
