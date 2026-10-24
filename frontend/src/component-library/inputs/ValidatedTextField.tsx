import InlineWarning from "./InlineWarning";
import TextField, { type TextFieldProps } from "./TextField";
import useFieldValidation from "../hooks/useFieldValidation";

type ValidatedTextFieldProps = Omit<
  TextFieldProps,
  "onBlur"
> & {
  validate: (value: string) => string;
  externalWarning?: string;
  warningDelayMs?: number;
};

export default function ValidatedTextField({
  value,
  onValueChange,
  validate,
  externalWarning = "",
  warningDelayMs = 500,
  ...inputProps
}: ValidatedTextFieldProps) {
  const { validateAfterPause, validateNow, warning } = useFieldValidation({
    value,
    validate,
    externalWarning,
    warningDelayMs,
  });

  return (
    <div className="validated-field">
      <TextField
        {...inputProps}
        value={value}
        onValueChange={(nextValue) => {
          onValueChange(nextValue);
          validateAfterPause(nextValue);
        }}
        onBlur={validateNow}
      />
      <InlineWarning message={warning} />
    </div>
  );
}
