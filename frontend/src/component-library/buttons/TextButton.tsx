import { forwardRef, type ButtonHTMLAttributes } from "react";

export type TextButtonSize = "s" | "m" | "l";

export type TextButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  size?: TextButtonSize;
};

const TextButton = forwardRef<HTMLButtonElement, TextButtonProps>(
  function TextButton(
    { className = "", size = "m", type = "button", ...buttonProps },
    ref,
  ) {
    const buttonClassName = `text-button text-button-${size}${className ? ` ${className}` : ""}`;

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
