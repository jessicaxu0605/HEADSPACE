import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";

export type CheckboxProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "checked" | "children" | "onChange" | "type"
> & {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children: ReactNode;
};

// A controlled native checkbox drawn as a circle, labelled by its children.
const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { checked, onCheckedChange, children, className = "", ...inputProps },
  ref,
) {
  const labelClassName = `checkbox${className ? ` ${className}` : ""}`;

  return (
    <label className={labelClassName}>
      <input
        {...inputProps}
        ref={ref}
        className="checkbox-input"
        type="checkbox"
        checked={checked}
        onChange={(event) => onCheckedChange(event.target.checked)}
      />
      <span className="checkbox-label">{children}</span>
    </label>
  );
});

export default Checkbox;
