import { forwardRef, type ButtonHTMLAttributes } from "react";

export type TextButtonProps = ButtonHTMLAttributes<HTMLButtonElement>;

const TextButton = forwardRef<HTMLButtonElement, TextButtonProps>(
  function TextButton({ className = "", type = "button", ...buttonProps }, ref) {
    const buttonClassName = `text-button${className ? ` ${className}` : ""}`;

    return (
      <button
        {...buttonProps}
        ref={ref}
        className={buttonClassName}
        type={type}
      />
    );
  },
);

export default TextButton;
