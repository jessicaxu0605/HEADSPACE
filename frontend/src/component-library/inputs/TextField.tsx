import type { InputHTMLAttributes } from "react";

export type TextFieldProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "onChange" | "value"
> & {
  value: string;
  onValueChange: (value: string) => void;
};

export default function TextField({
  value,
  onValueChange,
  className = "",
  ...inputProps
}: TextFieldProps) {
  const inputClassName = `text-field-input${className ? ` ${className}` : ""}`;

  return (
    <input
      {...inputProps}
      className={inputClassName}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
    />
  );
}
