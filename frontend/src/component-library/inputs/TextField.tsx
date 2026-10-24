import { forwardRef, type InputHTMLAttributes } from "react";

export type TextFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "onChange" | "value"
> & {
  value: string;
  onValueChange: (value: string) => void;
};

const TextField = forwardRef<HTMLInputElement, TextFieldProps>(
  function TextField({ value, onValueChange, className = "", ...inputProps }, ref) {
    const inputClassName = `text-field-input${className ? ` ${className}` : ""}`;

    return (
      <input
        {...inputProps}
        ref={ref}
        className={inputClassName}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
      />
    );
  },
);

export default TextField;
