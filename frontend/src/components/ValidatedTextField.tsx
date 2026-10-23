import {
  useEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
} from "react";

type ValidatedTextFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "onBlur" | "onChange" | "value"
> & {
  value: string;
  onValueChange: (value: string) => void;
  validate: (value: string) => string;
  externalWarning?: string;
  warningDelayMs?: number;
};

export function InlineWarning({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p className="field-warning" role="alert">
      {message}
    </p>
  );
}

export default function ValidatedTextField({
  value,
  onValueChange,
  validate,
  externalWarning = "",
  warningDelayMs = 500,
  ...inputProps
}: ValidatedTextFieldProps) {
  const [warning, setWarning] = useState("");
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
    setWarning("");
    const nextWarning = validate(nextValue);
    if (!nextWarning) return;
    warningTimeoutRef.current = window.setTimeout(() => {
      warningTimeoutRef.current = undefined;
      setWarning(nextWarning);
    }, warningDelayMs);
  };

  const validateNow = (): void => {
    clearPendingWarning();
    setWarning(validate(value));
  };

  return (
    <div className="validated-field">
      <input
        {...inputProps}
        value={value}
        onChange={(event) => {
          const nextValue = event.target.value;
          onValueChange(nextValue);
          validateAfterPause(nextValue);
        }}
        onBlur={validateNow}
      />
      <InlineWarning message={externalWarning || warning} />
    </div>
  );
}
