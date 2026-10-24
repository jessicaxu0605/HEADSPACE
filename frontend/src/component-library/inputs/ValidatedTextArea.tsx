import AutoGrowingTextArea, {
  type AutoGrowingTextAreaProps,
} from "./AutoGrowingTextArea";
import InlineWarning from "./InlineWarning";
import useFieldValidation from "../hooks/useFieldValidation";

type ValidatedTextAreaProps = Omit<AutoGrowingTextAreaProps, "onBlur"> & {
  validate: (value: string) => string;
  externalWarning?: string;
  warningDelayMs?: number;
};

export default function ValidatedTextArea({
  value,
  onValueChange,
  validate,
  externalWarning = "",
  warningDelayMs = 500,
  ...textareaProps
}: ValidatedTextAreaProps) {
  const { validateAfterPause, validateNow, warning } = useFieldValidation({
    value,
    validate,
    externalWarning,
    warningDelayMs,
  });

  return (
    <div className="validated-field">
      <AutoGrowingTextArea
        {...textareaProps}
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
