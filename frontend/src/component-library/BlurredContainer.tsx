import {
  forwardRef,
  type CSSProperties,
  type ForwardedRef,
  type HTMLAttributes,
} from "react";

export type BlurredEdge = "top" | "right" | "bottom" | "left";

type BlurredContainerProps = HTMLAttributes<HTMLElement> & {
  as?: "div" | "header";
  blurredEdges?: readonly BlurredEdge[];
  edgeBlurSize?: number | string;
};

const edgeSizeValue = (edgeBlurSize: number | string): string =>
  typeof edgeBlurSize === "number" ? `${edgeBlurSize}px` : edgeBlurSize;

const edgeMasks = (
  blurredEdges: readonly BlurredEdge[],
  edgeBlurSize: number | string,
): string[] => {
  const edges = new Set(blurredEdges);
  const size = edgeSizeValue(edgeBlurSize);
  const masks: string[] = [];
  const fadesLeft = edges.has("left");
  const fadesRight = edges.has("right");
  const fadesTop = edges.has("top");
  const fadesBottom = edges.has("bottom");

  if (fadesLeft && fadesRight) {
    masks.push(
      `linear-gradient(to right, transparent, #000 ${size}, #000 calc(100% - ${size}), transparent)`,
    );
  } else if (fadesLeft) {
    masks.push(`linear-gradient(to right, transparent, #000 ${size})`);
  } else if (fadesRight) {
    masks.push(
      `linear-gradient(to right, #000 calc(100% - ${size}), transparent)`,
    );
  }

  if (fadesTop && fadesBottom) {
    masks.push(
      `linear-gradient(to bottom, transparent, #000 ${size}, #000 calc(100% - ${size}), transparent)`,
    );
  } else if (fadesTop) {
    masks.push(`linear-gradient(to bottom, transparent, #000 ${size})`);
  } else if (fadesBottom) {
    masks.push(
      `linear-gradient(to bottom, #000 calc(100% - ${size}), transparent)`,
    );
  }

  return masks;
};

const BlurredContainer = forwardRef<HTMLElement, BlurredContainerProps>(
  function BlurredContainer(
    {
      as = "div",
      blurredEdges = [],
      edgeBlurSize = 24,
      className = "",
      style,
      ...props
    },
    ref,
  ) {
    const masks = edgeMasks(blurredEdges, edgeBlurSize);
    const maskImage = masks.length > 0 ? masks.join(", ") : undefined;
    const containerStyle = {
      ...style,
      WebkitMaskImage: maskImage,
      maskImage,
    } as CSSProperties;

    const containerClassName = `blurred-container${
      masks.length > 1 ? " has-intersecting-edge-masks" : ""
    }${className ? ` ${className}` : ""}`;

    if (as === "header") {
      return (
        <header
          {...props}
          ref={ref}
          className={containerClassName}
          style={containerStyle}
        />
      );
    }

    return (
      <div
        {...props}
        ref={ref as ForwardedRef<HTMLDivElement>}
        className={containerClassName}
        style={containerStyle}
      />
    );
  },
);

export default BlurredContainer;
