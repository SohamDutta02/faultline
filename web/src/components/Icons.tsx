type IconProps = { size?: number; title?: string };

function Svg({ size = 14, title, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

export const Check = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 8.5l3.2 3L13 4.5" />
  </Svg>
);

export const Cross = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
);

export const Alert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 2.5l6 11H2l6-11z" />
    <path d="M8 6.5v3M8 11.6v.1" />
  </Svg>
);

export const Loop = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13 8a5 5 0 1 1-1.5-3.5" />
    <path d="M13 2.5V5h-2.5" />
  </Svg>
);

export const Minus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8h8" />
  </Svg>
);

/** The fault mark: a break in a line. */
export const Fault = (p: IconProps) => (
  <Svg {...p}>
    <path d="M1.5 8.5h3l2-4.5 3 8 2-3.5h3" />
  </Svg>
);

export const Play = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 3.5v9l7-4.5-7-4.5z" fill="currentColor" stroke="none" />
  </Svg>
);

export const Stop = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor" stroke="none" />
  </Svg>
);

export const Retry = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 8a5 5 0 0 1 8.5-3.5L13 6" />
    <path d="M13 2.5V6H9.5" />
  </Svg>
);

export const Mark = ({ size = 18 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
    <rect width="32" height="32" rx="6" fill="currentColor" />
    <path
      d="M5 17h6l3-7 4 13 3-6h6"
      fill="none"
      stroke="var(--warning)"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
