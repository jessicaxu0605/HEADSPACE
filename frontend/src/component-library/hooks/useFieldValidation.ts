import { useEffect, useRef, useState } from "react";

type UseFieldValidationOptions = {
  value: string;
  validate: (value: string) => string;
  externalWarning?: string;
  warningDelayMs?: number;
};

export default function useFieldValidation({
  value,
  validate,
  externalWarning = "",
  warningDelayMs = 500,
}: UseFieldValidationOptions) {
  const [localWarning, setLocalWarning] = useState("");
  const warningTimeoutRef = useRef<number | undefined>(undefined);

  const clearPendingWarning = (): void => {
    window.clearTimeout(warningTimeoutRef.current);
    warningTimeoutRef.current = undefined;
  };

  useEffect(
    () => () => window.clearTimeout(warningTimeoutRef.current),
    [],
  );

  const validateAfterPause = (nextValue: string): void => {
    clearPendingWarning();
    setLocalWarning("");
    const nextWarning = validate(nextValue);
    if (!nextWarning) return;
    warningTimeoutRef.current = window.setTimeout(() => {
      warningTimeoutRef.current = undefined;
      setLocalWarning(nextWarning);
    }, warningDelayMs);
  };

  const validateNow = (): void => {
    clearPendingWarning();
    setLocalWarning(validate(value));
  };

  return {
    validateAfterPause,
    validateNow,
    warning: externalWarning || localWarning,
  };
}
